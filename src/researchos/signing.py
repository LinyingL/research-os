"""Signatures: is a snapshot really the PI's? (DESIGN.md 12.5; task V8 phase 4).

Research OS signs what a person writes to the shared drive with an SSH
signing key, through `ssh-keygen` (part of macOS and every Linux -- no new
dependency):

- **The key** is created on first need, `ssh-keygen -t ed25519 -N "" -C
  researchos`, at `~/.researchos/keys/id_ed25519` (0600; the folder 0700).
  `researchos me key` prints its public half for the PI to add with
  `researchos team add`; the PI's own is written into `team.toml` by
  `researchos team init`.
- **Signing** is `ssh-keygen -Y sign -f <key> -n researchos` over the bytes
  of a manifest, a proposal or an outcome (read on stdin; the signature is
  written beside them as `<file>.sig`, inside the same write-once
  directory).
- **Verifying** is `ssh-keygen -Y verify -f <allowed_signers> -I <principal>
  -n researchos -s <sig>`, the allowed-signers file built in a private
  temporary folder from the ONE key the reader expects (from `team.toml`,
  or the PI's key a member pinned when joining). The principal written
  there is a fixed word, never the member id: an id may contain `*`, `?` or
  `!`, which `allowed_signers` reads as patterns.

`ssh-keygen` is always run by an absolute path, never through a shell, with
a clean minimal environment (no agent socket, a private HOME) and a
timeout. Where it is missing, that is a stated state (`tool() is None`):
nothing can be signed or verified, and every status says that snapshots
cannot be verified and only the shared drive's own sharing permissions
protect them (12.5).

**Who must sign.** One rule, on both sides: a person whose key is listed in
`team.toml` signs everything they write to the shared drive, and a reader
that knows that key and has `ssh-keygen` accepts only what verifies under
it (`Check.trusted`). A person listed without a key (a team file written
before keys existed) writes unsigned, and their writing is shown as
"cannot be verified; relying on the sharing permissions" -- the 12.5
fallback, never silently as verified.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from researchos import paths

NAMESPACE = "researchos"
KEYS_DIRNAME = "keys"
KEY_FILENAME = "id_ed25519"
KEY_COMMENT = "researchos"
SIG_SUFFIX = ".sig"
KEY_TYPE = "ssh-ed25519"
TIMEOUT_SECONDS = 30
#: Where ssh-keygen is looked for -- explicit paths only, never $PATH.
CANDIDATES = ("/usr/bin/ssh-keygen", "/usr/local/bin/ssh-keygen", "/opt/homebrew/bin/ssh-keygen", "/bin/ssh-keygen")
_PRINCIPAL = "researchos-signer"
_SIG_BEGIN = b"-----BEGIN SSH SIGNATURE-----"
_KEY_RE = re.compile(r"^(ssh-ed25519) ([A-Za-z0-9+/]+={0,2})(?: .*)?$")

#: Product language (8.8) for each verification state.
CHECK_MESSAGES = {
    "verified": "签名已验证",
    "no_key": "成员名单里没有这个人的公钥：无法验证签名，只能依靠公盘的共享权限",
    "no_tool": "这台电脑没有 ssh-keygen：无法验证签名，只能依靠公盘的共享权限",
    "unsigned": "没有签名：无法确认是谁写的",
    "bad": "签名不对：不是名单上这个人的密钥签的，或者签名之后内容被改过",
}
NO_TOOL_WARNING = "这台电脑没有 ssh-keygen：快照无法验证，只能依靠公盘的共享权限"


class SigningError(Exception):
    """Signing (or making a key) failed; nothing was written. `code`:
    `no_tool`, `no_local_key`, `key_mismatch`, `invalid_key`, `failed`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

    @property
    def message_zh(self) -> str:
        return {
            "no_tool": "这台电脑没有 ssh-keygen，无法签名；成员名单里列着你的公钥，所以没有签名的内容不会被接受",
            "no_local_key": "成员名单里列着你的公钥，但这台电脑上没有对应的密钥（~/.researchos/keys/）",
            "key_mismatch": "这台电脑上的密钥和成员名单里你的公钥不一致",
            "invalid_key": "公钥格式不对（需要 ssh-ed25519 公钥的一行文字）",
            "failed": "ssh-keygen 运行失败",
        }.get(self.code, "无法签名")


# -- the program --------------------------------------------------------------------


def tool() -> str | None:
    """The absolute path of ssh-keygen, or None when it is missing."""
    for candidate in CANDIDATES:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _run(args: list[str], *, data: bytes | None = None, home: Path) -> subprocess.CompletedProcess:
    program = tool()
    if program is None:
        raise SigningError("no_tool", "ssh-keygen is not installed on this machine")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "LC_ALL": "C", "LANG": "C"}
    try:
        return subprocess.run(
            [program, *args], input=data if data is not None else b"", capture_output=True, env=env,
            cwd=str(home), timeout=TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SigningError("failed", f"ssh-keygen did not finish within {TIMEOUT_SECONDS} seconds") from exc
    except OSError as exc:
        raise SigningError("failed", f"ssh-keygen could not be run: {exc}") from exc


# -- keys ---------------------------------------------------------------------------


def keys_dir() -> Path:
    return paths.researchos_home() / KEYS_DIRNAME


def key_path() -> Path:
    return keys_dir() / KEY_FILENAME


def parse_public_key(text: str) -> str:
    """An ssh-ed25519 public key as `"ssh-ed25519 <base64>"` (the comment
    dropped), checked: one line, the blob decodes and names its own type.
    Raises `SigningError("invalid_key")`."""
    if not isinstance(text, str):
        raise SigningError("invalid_key", "a public key is one line of text")
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if len(lines) != 1:
        raise SigningError("invalid_key", "a public key is exactly one line")
    m = _KEY_RE.match(lines[0])
    if m is None:
        raise SigningError("invalid_key", f"not an {KEY_TYPE} public key: {lines[0][:40]!r}")
    try:
        blob = base64.b64decode(m.group(2), validate=True)
    except ValueError as exc:
        raise SigningError("invalid_key", f"the key is not base64: {exc}") from exc
    name = KEY_TYPE.encode("ascii")
    if len(blob) < 4 + len(name) or int.from_bytes(blob[:4], "big") != len(name) or blob[4:4 + len(name)] != name:
        raise SigningError("invalid_key", "the key's content does not name its type")
    return f"{m.group(1)} {m.group(2)}"


def fingerprint(public_key: str) -> str:
    """`SHA256:<base64, unpadded>` -- what `ssh-keygen -l` prints, computed
    here so that showing a fingerprint never needs the program."""
    blob = base64.b64decode(parse_public_key(public_key).split(" ")[1])
    return "SHA256:" + base64.b64encode(hashlib.sha256(blob).digest()).decode("ascii").rstrip("=")


def local_public_key() -> str | None:
    """This machine's public key, or None when there is none yet."""
    try:
        text = (keys_dir() / (KEY_FILENAME + ".pub")).read_text(encoding="utf-8")
    except OSError:
        return None
    if not key_path().is_file():
        return None
    try:
        return parse_public_key(text)
    except SigningError:
        return None


def ensure_key() -> str:
    """This machine's public key, creating the key pair on first need
    (module docstring). Raises `SigningError("no_tool")` without ssh-keygen."""
    existing = local_public_key()
    if existing is not None:
        return existing
    directory = keys_dir()
    directory.mkdir(parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    if key_path().exists() or (directory / (KEY_FILENAME + ".pub")).exists():
        raise SigningError("failed", f"{directory} holds a key Research OS cannot read; it is left as it is")
    tmp = directory / f".{KEY_FILENAME}.tmp-{secrets.token_hex(6)}"
    try:
        done = _run(["-q", "-t", "ed25519", "-N", "", "-C", KEY_COMMENT, "-f", str(tmp)], home=directory)
        if done.returncode != 0 or not tmp.is_file():
            raise SigningError("failed", "ssh-keygen could not make a key: " + done.stderr.decode("utf-8", "replace").strip())
        os.chmod(tmp, 0o600)
        # Linked into place, never over a key another process just made.
        try:
            os.link(Path(str(tmp) + ".pub"), directory / (KEY_FILENAME + ".pub"))
            os.link(tmp, key_path())
        except FileExistsError:
            pass
    finally:
        for leftover in (tmp, Path(str(tmp) + ".pub")):
            try:
                leftover.unlink()
            except FileNotFoundError:
                pass
    got = local_public_key()
    if got is None:
        raise SigningError("failed", f"the key made in {directory} cannot be read back")
    return got


# -- signing and verifying -------------------------------------------------------------


def sign(data: bytes) -> bytes:
    """A signature over `data` with this machine's key (which must exist)."""
    if local_public_key() is None:
        raise SigningError("no_local_key", f"there is no key in {keys_dir()}")
    with tempfile.TemporaryDirectory(prefix="researchos-sign-") as private:
        done = _run(["-Y", "sign", "-f", str(key_path()), "-n", NAMESPACE], data=data, home=Path(private))
    if done.returncode != 0 or not done.stdout.startswith(_SIG_BEGIN):
        raise SigningError("failed", "ssh-keygen could not sign: " + done.stderr.decode("utf-8", "replace").strip())
    return done.stdout


def verify(data: bytes, signature: bytes, public_key: str) -> bool:
    """Whether `signature` is a valid signature over `data` by exactly
    `public_key`, in Research OS's namespace."""
    key = parse_public_key(public_key)
    with tempfile.TemporaryDirectory(prefix="researchos-verify-") as private:
        home = Path(private)
        allowed, sig = home / "allowed_signers", home / "data.sig"
        allowed.write_text(f'{_PRINCIPAL} namespaces="{NAMESPACE}" {key}\n', encoding="utf-8")
        sig.write_bytes(signature)
        done = _run(["-Y", "verify", "-f", str(allowed), "-I", _PRINCIPAL, "-n", NAMESPACE, "-s", str(sig)],
                    data=data, home=home)
    return done.returncode == 0


@dataclass(frozen=True)
class Check:
    """One signature, as a reader judged it. `state`: verified | no_key |
    no_tool | unsigned | bad. `trusted`: whether it may be acted on."""

    state: str
    signer: str | None = None
    fingerprint: str | None = None

    @property
    def trusted(self) -> bool:
        return self.state in ("verified", "no_key", "no_tool")

    @property
    def message_zh(self) -> str:
        return CHECK_MESSAGES[self.state]

    def payload(self) -> dict[str, object]:
        return {"state": self.state, "trusted": self.trusted, "signer": self.signer,
                "fingerprint": self.fingerprint, "message": self.message_zh}


def check(data: bytes, signature: bytes | None, public_key: str | None, *, signer: str | None = None) -> Check:
    """Judge one signature against the key the reader expects (module
    docstring, "Who must sign")."""
    if not public_key:
        return Check("no_key", signer)
    try:
        print_ = fingerprint(public_key)
    except SigningError:
        return Check("bad", signer)
    if tool() is None:
        return Check("no_tool", signer, print_)
    if signature is None:
        return Check("unsigned", signer, print_)
    try:
        good = verify(data, signature, public_key)
    except SigningError:
        good = False
    return Check("verified" if good else "bad", signer, print_)


class Verifier:
    """Checks what members wrote against the keys a reader trusts: the
    team's (`team.toml`), with the PI's replaced by the key a member pinned
    when joining."""

    def __init__(self, keys: Mapping[str, str | None], pi: str | None = None) -> None:
        self.keys = dict(keys)
        self.pi = pi

    def check(self, signer: str, data: bytes, signature: bytes | None) -> Check:
        return check(data, signature, self.keys.get(signer), signer=signer)


def preflight(member: str, listed_key: str | None) -> None:
    """Raise `SigningError` now if `member` will not be able to sign
    (`sign_as`) -- asked before anything is written."""
    if not listed_key:
        return
    if tool() is None:
        raise SigningError("no_tool", f"{member}'s key is listed in team.toml but ssh-keygen is missing: cannot sign")
    local = local_public_key()
    if local is None:
        raise SigningError("no_local_key", f"{member}'s key is listed in team.toml but there is no key in {keys_dir()}")
    if parse_public_key(listed_key) != local:
        raise SigningError("key_mismatch", f"the key in {keys_dir()} ({fingerprint(local)}) is not the one team.toml "
                                           f"lists for {member} ({fingerprint(listed_key)})")


def sign_as(member: str, listed_key: str | None, data: bytes) -> bytes | None:
    """The signature `member` writes with `data`: None when the team lists
    no key for them (module docstring); otherwise a signature by this
    machine's key, which must be the listed one -- refused, with the reason,
    when it cannot be."""
    if not listed_key:
        return None
    preflight(member, listed_key)
    return sign(data)

