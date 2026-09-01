from __future__ import annotations

import errno
import json
import os
import struct
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import (
    AESGCM,
    AESGCMSIV,
    AESOCB3,
    AESSIV,
    ChaCha20Poly1305,
)
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


VERSION = 4
SUPPORTED_VERSIONS = {2, 3, 4}
SALT_SIZE = 16
NONCE_SIZE = 12
KEY_SIZE = 32
NAME_TOKEN_BYTES = 16
NAME_PREFIX = "xenon-"
XENON_DIRNAME = ".xenon"
MAGIC = b"XENON\x02"
MANIFEST_AAD = b"xenon-manifest"

CIPHER_CHACHA = "ChaCha20-Poly1305"
CIPHER_XCHACHA = "XChaCha20-Poly1305"
CIPHER_AESGCM = "AES-256-GCM"
CIPHER_AES128GCM = "AES-128-GCM"
CIPHER_AESOCB3 = "AES-256-OCB3"
CIPHER_AESGCMSIV = "AES-256-GCM-SIV"
CIPHER_AESSIV = "AES-256-SIV"


class AEADCipher(Protocol):
    def encrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes: ...
    def decrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes: ...


def _rotl32(value: int, bits: int) -> int:
    value &= 0xFFFFFFFF
    return ((value << bits) | (value >> (32 - bits))) & 0xFFFFFFFF


def _hchacha20(key: bytes, nonce: bytes) -> bytes:
    """Derive a 32-byte subkey (IETF draft-irtf-cfrg-xchacha)."""
    if len(key) != 32:
        raise ValueError("HChaCha20 key must be 32 bytes.")
    if len(nonce) != 16:
        raise ValueError("HChaCha20 nonce must be 16 bytes.")

    state = list(struct.unpack("<16I", b"expand 32-byte k" + key + nonce))
    for _ in range(10):
        # column rounds
        for a, b, c, d in ((0, 4, 8, 12), (1, 5, 9, 13), (2, 6, 10, 14), (3, 7, 11, 15)):
            state[a] = (state[a] + state[b]) & 0xFFFFFFFF
            state[d] = _rotl32(state[d] ^ state[a], 16)
            state[c] = (state[c] + state[d]) & 0xFFFFFFFF
            state[b] = _rotl32(state[b] ^ state[c], 12)
            state[a] = (state[a] + state[b]) & 0xFFFFFFFF
            state[d] = _rotl32(state[d] ^ state[a], 8)
            state[c] = (state[c] + state[d]) & 0xFFFFFFFF
            state[b] = _rotl32(state[b] ^ state[c], 7)
        # diagonal rounds
        for a, b, c, d in ((0, 5, 10, 15), (1, 6, 11, 12), (2, 7, 8, 13), (3, 4, 9, 14)):
            state[a] = (state[a] + state[b]) & 0xFFFFFFFF
            state[d] = _rotl32(state[d] ^ state[a], 16)
            state[c] = (state[c] + state[d]) & 0xFFFFFFFF
            state[b] = _rotl32(state[b] ^ state[c], 12)
            state[a] = (state[a] + state[b]) & 0xFFFFFFFF
            state[d] = _rotl32(state[d] ^ state[a], 8)
            state[c] = (state[c] + state[d]) & 0xFFFFFFFF
            state[b] = _rotl32(state[b] ^ state[c], 7)
    return struct.pack("<8I", *state[0:4], *state[12:16])


class AesSivAdapter:
    """Adapt AESSIV (no nonce argument; AAD is a list) to the AEADCipher shape."""

    def __init__(self, key: bytes) -> None:
        self._inner = AESSIV(key)

    def encrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes:
        aad: list[bytes] = []
        if associated_data:
            aad.append(associated_data)
        aad.append(nonce)
        return self._inner.encrypt(data, aad)

    def decrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes:
        aad: list[bytes] = []
        if associated_data:
            aad.append(associated_data)
        aad.append(nonce)
        return self._inner.decrypt(data, aad)


class XChaCha20Poly1305:
    """XChaCha20-Poly1305 AEAD with a 24-byte nonce."""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("XChaCha20-Poly1305 key must be 32 bytes.")
        self._key = key

    def _inner(self, nonce: bytes) -> tuple[ChaCha20Poly1305, bytes]:
        if len(nonce) != 24:
            raise ValueError("XChaCha20-Poly1305 nonce must be 24 bytes.")
        subkey = _hchacha20(self._key, nonce[:16])
        inner_nonce = b"\x00\x00\x00\x00" + nonce[16:]
        return ChaCha20Poly1305(subkey), inner_nonce

    def encrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes:
        inner, inner_nonce = self._inner(nonce)
        return inner.encrypt(inner_nonce, data, associated_data)

    def decrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes:
        inner, inner_nonce = self._inner(nonce)
        return inner.decrypt(inner_nonce, data, associated_data)


@dataclass(frozen=True)
class CipherSpec:
    name: str
    aliases: tuple[str, ...]
    key_size: int
    nonce_size: int
    summary: str
    factory: Callable[[bytes], AEADCipher]


CIPHER_SPECS: tuple[CipherSpec, ...] = (
    CipherSpec(
        CIPHER_CHACHA,
        aliases=("chacha20-poly1305", "chacha20poly1305", "chacha"),
        key_size=32,
        nonce_size=12,
        summary="fast in software",
        factory=ChaCha20Poly1305,
    ),
    CipherSpec(
        CIPHER_XCHACHA,
        aliases=("xchacha20-poly1305", "xchacha20poly1305", "xchacha"),
        key_size=32,
        nonce_size=24,
        summary="192-bit nonce, safer random IVs",
        factory=XChaCha20Poly1305,
    ),
    CipherSpec(
        CIPHER_AESGCM,
        aliases=("aes-256-gcm", "aes256gcm", "aes-gcm", "aes"),
        key_size=32,
        nonce_size=12,
        summary="hardware AES-NI",
        factory=AESGCM,
    ),
    CipherSpec(
        CIPHER_AES128GCM,
        aliases=("aes-128-gcm", "aes128gcm"),
        key_size=16,
        nonce_size=12,
        summary="faster AES, 128-bit key",
        factory=AESGCM,
    ),
    CipherSpec(
        CIPHER_AESOCB3,
        aliases=("aes-256-ocb3", "aes256ocb3", "aes-ocb3", "ocb3"),
        key_size=32,
        nonce_size=12,
        summary="high-speed AEAD",
        factory=AESOCB3,
    ),
    CipherSpec(
        CIPHER_AESGCMSIV,
        aliases=("aes-256-gcm-siv", "aes256gcmsiv", "aes-gcm-siv", "gcm-siv"),
        key_size=32,
        nonce_size=12,
        summary="nonce-misuse resistant",
        factory=AESGCMSIV,
    ),
    CipherSpec(
        CIPHER_AESSIV,
        aliases=("aes-256-siv", "aes256siv", "aes-siv", "siv"),
        key_size=64,
        nonce_size=16,
        summary="nonce-misuse resistant (SIV)",
        factory=AesSivAdapter,
    ),
)

SUPPORTED_CIPHERS = tuple(spec.name for spec in CIPHER_SPECS)


class VaultAEAD:
    """AEAD wrapper that records the nonce size used in Xenon blobs."""

    def __init__(self, inner: AEADCipher, nonce_size: int) -> None:
        self._inner = inner
        self.nonce_size = nonce_size

    def encrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes:
        return self._inner.encrypt(nonce, data, associated_data)

    def decrypt(self, nonce: bytes, data: bytes, associated_data: bytes | None) -> bytes:
        return self._inner.decrypt(nonce, data, associated_data)


def _cipher_alias_map() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for spec in CIPHER_SPECS:
        mapping[spec.name.lower()] = spec.name
        for alias in spec.aliases:
            mapping[alias.lower()] = spec.name
    return mapping


_CIPHER_ALIASES = _cipher_alias_map()


def normalize_cipher(name: str | None) -> str:
    if not name:
        return CIPHER_CHACHA
    key = name.strip().lower()
    if key in _CIPHER_ALIASES:
        return _CIPHER_ALIASES[key]
    raise ValueError(
        f"Unsupported cipher: {name}. Choose: {', '.join(SUPPORTED_CIPHERS)}"
    )


def get_cipher_spec(name: str | None) -> CipherSpec:
    canonical = normalize_cipher(name)
    for spec in CIPHER_SPECS:
        if spec.name == canonical:
            return spec
    raise ValueError(f"Unsupported cipher: {name}")


def make_cipher(
    algorithm: str,
    key: bytes,
    *,
    nonce_size: int | None = None,
) -> VaultAEAD:
    spec = get_cipher_spec(algorithm)
    if len(key) != spec.key_size:
        raise ValueError(
            f"{spec.name} key must be {spec.key_size} bytes, got {len(key)}."
        )
    size = nonce_size if nonce_size is not None else spec.nonce_size
    return VaultAEAD(spec.factory(key), size)


def derive_key(
    password: str,
    salt: bytes,
    *,
    key_file: Path | None = None,
    key_size: int = KEY_SIZE,
    cipher_name: str = CIPHER_CHACHA,
) -> bytes:
    if not password:
        raise ValueError("Password cannot be empty.")

    kdf = Argon2id(
        salt=salt,
        length=KEY_SIZE,
        iterations=3,
        lanes=4,
        memory_cost=64 * 1024,
    )
    key = kdf.derive(password.encode("utf-8"))

    if key_file is not None:
        key_path = Path(key_file).expanduser()
        if not key_path.is_file():
            raise FileNotFoundError(f"Key file not found: {key_path}")
        material = key_path.read_bytes()
        if not material:
            raise ValueError(f"Key file is empty: {key_path}")
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=KEY_SIZE,
            salt=salt,
            info=b"xenon-optional-keyfile",
        ).derive(key + material)

    if key_size != KEY_SIZE:
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=key_size,
            salt=salt,
            info=b"xenon-cipher-key:" + cipher_name.encode("utf-8"),
        ).derive(key)

    return key


def load_optional_key_file(path: str | Path | None) -> Path | None:
    if not path:
        return None
    return Path(path).expanduser()


def xenon_dir(root: Path) -> Path:
    return root / XENON_DIRNAME


def manifest_path(root: Path) -> Path:
    return xenon_dir(root) / "manifest.json"


def is_locked(root: Path) -> bool:
    return manifest_path(root).is_file()


def iter_target_files(
    root: Path,
    *,
    policy: Any | None = None,
    show_progress: bool = False,
    label: str = "Scanning",
) -> list[Path]:
    """Return regular files under root (streaming walk + exclusion policy)."""
    from xenon.walk import collect_files

    return collect_files(
        root,
        policy=policy,
        label=label,
        show_progress=show_progress,
    )


def safe_file_size(path: Path) -> int | None:
    try:
        return path.lstat().st_size
    except OSError:
        return None


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".xenon-tmp",
        dir=path.parent,
    )

    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def pack_encrypted(nonce: bytes, ciphertext: bytes) -> bytes:
    return MAGIC + nonce + ciphertext


def unpack_encrypted(
    blob: bytes,
    nonce_size: int = NONCE_SIZE,
) -> tuple[bytes, bytes]:
    header = len(MAGIC) + nonce_size

    if len(blob) < header:
        raise ValueError("Encrypted file is too small.")

    if not blob.startswith(MAGIC):
        raise ValueError("File is not a Xenon ciphertext blob.")

    nonce = blob[len(MAGIC):len(MAGIC) + nonce_size]
    ciphertext = blob[len(MAGIC) + nonce_size:]
    return nonce, ciphertext


def encrypt_inventory(
    cipher: VaultAEAD,
    files: list[dict],
    dirs: list[dict] | None = None,
) -> str:
    plaintext = json.dumps(
        {"files": files, "dirs": dirs or []},
        separators=(",", ":"),
    ).encode("utf-8")
    nonce = os.urandom(cipher.nonce_size)
    ciphertext = cipher.encrypt(nonce, plaintext, MANIFEST_AAD)
    return pack_encrypted(nonce, ciphertext).hex()


def decrypt_inventory_payload(cipher: VaultAEAD, inventory_hex: str) -> dict:
    blob = bytes.fromhex(inventory_hex)
    nonce, ciphertext = unpack_encrypted(blob, cipher.nonce_size)
    plaintext = cipher.decrypt(nonce, ciphertext, MANIFEST_AAD)
    payload = json.loads(plaintext.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid encrypted inventory.")
    return payload


def decrypt_inventory(cipher: VaultAEAD, inventory_hex: str) -> list[dict]:
    payload = decrypt_inventory_payload(cipher, inventory_hex)
    files = payload.get("files")
    if not isinstance(files, list):
        raise ValueError("Invalid encrypted inventory.")
    return files


def resolve_inventory(
    manifest: dict,
    cipher: VaultAEAD,
) -> tuple[list[dict], list[dict]]:
    version = manifest.get("version")

    if version >= 3:
        inventory = manifest.get("inventory")
        if not inventory:
            raise ValueError("Manifest is missing encrypted inventory.")
        payload = decrypt_inventory_payload(cipher, inventory)
        files = payload.get("files")
        dirs = payload.get("dirs") or []
        if not isinstance(files, list):
            raise ValueError("Invalid encrypted inventory.")
        if not isinstance(dirs, list):
            dirs = []
        return files, dirs

    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("Manifest is missing file list.")
    return files, []


def resolve_file_list(manifest: dict, cipher: VaultAEAD) -> list[dict]:
    files, _dirs = resolve_inventory(manifest, cipher)
    return files


def stored_path_of(entry: dict) -> str:
    stored = entry.get("stored")
    if isinstance(stored, str) and stored:
        return stored
    return entry["path"]


def _parent_rels(relative: str) -> list[str]:
    parts = [part for part in relative.split("/") if part]
    return ["/".join(parts[:index]) for index in range(1, len(parts))]


def _opaque_token() -> str:
    return NAME_PREFIX + os.urandom(NAME_TOKEN_BYTES).hex()


def build_name_map(
    file_rels: list[str],
    dir_rels: list[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Map original relative paths to opaque stored paths."""
    used: dict[str, set[str]] = {"": set()}

    def alloc(parent_stored: str) -> str:
        bucket = used.setdefault(parent_stored, set())
        for _ in range(64):
            token = _opaque_token()
            if token not in bucket and token != XENON_DIRNAME:
                bucket.add(token)
                return token
        raise RuntimeError("Failed to allocate an opaque filename.")

    dir_stored: dict[str, str] = {}
    for rel in sorted(set(dir_rels), key=lambda item: (item.count("/"), item)):
        parent, _sep, _name = rel.rpartition("/")
        parent_stored = dir_stored.get(parent, "") if parent else ""
        token = alloc(parent_stored)
        dir_stored[rel] = f"{parent_stored}/{token}" if parent_stored else token

    file_stored: dict[str, str] = {}
    for rel in file_rels:
        parent, _sep, _name = rel.rpartition("/")
        parent_stored = dir_stored.get(parent, "") if parent else ""
        token = alloc(parent_stored)
        file_stored[rel] = f"{parent_stored}/{token}" if parent_stored else token

    return file_stored, dir_stored


def _candidate_paths(root: Path, entry: dict) -> list[Path]:
    original = entry["path"]
    stored = stored_path_of(entry)
    orig_name = Path(original).name
    stored_name = Path(stored).name
    parent_orig = str(Path(original).parent)
    parent_stored = str(Path(stored).parent)
    if parent_orig == ".":
        parent_orig = ""
    if parent_stored == ".":
        parent_stored = ""

    raw = [
        root / stored,
        root / original,
        (root / parent_orig / stored_name) if parent_orig else root / stored_name,
        (root / parent_stored / orig_name) if parent_stored else root / orig_name,
    ]
    unique: list[Path] = []
    seen: set[str] = set()
    for path in raw:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def locate_entry_path(root: Path, entry: dict, *, want_file: bool) -> Path:
    for path in _candidate_paths(root, entry):
        try:
            if want_file and path.is_file():
                return path
            if not want_file and path.is_dir():
                return path
        except OSError:
            continue
    kind = "file" if want_file else "directory"
    raise FileNotFoundError(f"Missing {kind}: {entry.get('path')}")


def _rename_basename(path: Path, new_name: str) -> Path:
    dest = path.parent / new_name
    if dest == path:
        return path
    if dest.exists():
        raise FileExistsError(f"Name collision while renaming: {dest}")
    os.rename(path, dest)
    return dest


def hide_entry_names(
    root: Path,
    files: list[dict],
    dirs: list[dict],
    progress: Any | None = None,
) -> None:
    """Replace plaintext file and folder names with opaque tokens."""
    for entry in files:
        original = entry["path"]
        stored_name = Path(stored_path_of(entry)).name
        src = root / original
        if progress is not None:
            progress.update(1, suffix=original[:48])
        if not src.is_file():
            continue
        _rename_basename(src, stored_name)

    for entry in sorted(dirs, key=lambda item: item["path"].count("/"), reverse=True):
        src = root / entry["path"]
        if progress is not None:
            progress.update(1, suffix=entry["path"][:48])
        try:
            if not src.is_dir() or src.is_symlink():
                continue
        except OSError:
            continue
        _rename_basename(src, Path(stored_path_of(entry)).name)


def restore_entry_names(root: Path, files: list[dict], dirs: list[dict]) -> None:
    """Restore original file and folder names from inventory mappings."""
    for entry in files:
        stored = stored_path_of(entry)
        original = entry["path"]
        if stored == original:
            continue
        current = locate_entry_path(root, entry, want_file=True)
        _rename_basename(current, Path(original).name)

    for entry in sorted(
        dirs,
        key=lambda item: stored_path_of(item).count("/"),
        reverse=True,
    ):
        stored = stored_path_of(entry)
        original = entry["path"]
        if stored == original:
            continue
        try:
            current = locate_entry_path(root, entry, want_file=False)
        except FileNotFoundError:
            continue
        try:
            if current.is_symlink():
                continue
        except OSError:
            continue
        _rename_basename(current, Path(original).name)

    for entry in dirs:
        dest = root / entry["path"]
        dest.mkdir(parents=True, exist_ok=True)


def encrypt_in_place(
    path: Path,
    cipher: VaultAEAD,
    relative_path: str,
) -> dict:
    plaintext = path.read_bytes()

    if plaintext.startswith(MAGIC):
        raise ValueError(
            f"Refusing to encrypt already-packed file: {relative_path}"
        )

    nonce = os.urandom(cipher.nonce_size)
    aad = relative_path.encode("utf-8")
    ciphertext = cipher.encrypt(nonce, plaintext, aad)
    _atomic_write(path, pack_encrypted(nonce, ciphertext))
    return {"path": relative_path}


def decrypt_in_place(
    path: Path,
    cipher: VaultAEAD,
    relative_path: str,
) -> None:
    blob = path.read_bytes()
    nonce, ciphertext = unpack_encrypted(blob, cipher.nonce_size)
    aad = relative_path.encode("utf-8")
    plaintext = cipher.decrypt(nonce, ciphertext, aad)
    _atomic_write(path, plaintext)


def lock_directory(
    root: Path,
    password: str,
    *,
    cipher_name: str = CIPHER_CHACHA,
    key_file: Path | None = None,
    policy: Any | None = None,
) -> None:
    root = root.resolve()
    cipher_name = normalize_cipher(cipher_name)

    if not root.is_dir():
        raise ValueError(f"Directory does not exist: {root}")

    if is_locked(root):
        raise FileExistsError(
            f"Directory is already locked: {manifest_path(root)}"
        )

    files = iter_target_files(
        root,
        policy=policy,
        show_progress=True,
        label="Scanning",
    )
    if not files:
        raise ValueError("Directory contains no files to encrypt.")

    from xenon.walk import collect_directories

    file_rels = [path.relative_to(root).as_posix() for path in files]
    dir_rels = {
        path.relative_to(root).as_posix()
        for path in collect_directories(root, policy=policy)
    }
    for relative in file_rels:
        dir_rels.update(_parent_rels(relative))
    file_stored, dir_stored = build_name_map(file_rels, sorted(dir_rels))
    dir_entries = [
        {"path": original, "stored": stored}
        for original, stored in dir_stored.items()
    ]

    spec = get_cipher_spec(cipher_name)
    salt = os.urandom(SALT_SIZE)
    key = derive_key(
        password,
        salt,
        key_file=key_file,
        key_size=spec.key_size,
        cipher_name=cipher_name,
    )
    cipher = make_cipher(cipher_name, key)

    meta = xenon_dir(root)
    meta.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = []

    print(f"Locking in place: {root}")
    print()

    from xenon.progress import Progress

    progress = Progress("Encrypting", total=len(files))
    try:
        skipped = 0
        for path in files:
            relative = path.relative_to(root).as_posix()
            progress.update(1, suffix=relative[:48])
            try:
                entry = encrypt_in_place(path, cipher, relative)
                entry["stored"] = file_stored[relative]
                entries.append(entry)
            except FileNotFoundError:
                skipped += 1
            except OSError as exc:
                if exc.errno in {errno.ENOENT, errno.ESTALE}:
                    skipped += 1
                else:
                    raise

        if not entries:
            raise ValueError(
                "No files could be encrypted "
                "(all targets vanished or were unreadable)."
            )

        manifest: dict[str, Any] = {
            "version": VERSION,
            "mode": "in-place",
            "names": {"encrypted": True},
            "kdf": {
                "algorithm": "Argon2id",
                "salt": salt.hex(),
                "length": KEY_SIZE,
                "iterations": 3,
                "lanes": 4,
                "memory_cost": 64 * 1024,
                "key_file": bool(key_file),
            },
            "cipher": {
                "algorithm": cipher_name,
                "nonce_size": spec.nonce_size,
                "key_size": spec.key_size,
                "magic": MAGIC.hex(),
            },
            "inventory": encrypt_inventory(cipher, entries, dir_entries),
        }

        manifest_path(root).write_text(
            json.dumps(manifest, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        print()
        print(
            "ERROR during lock. Some files may already be encrypted. "
            "Do not delete .xenon/; unlock with the same password once "
            "a manifest exists, or restore from backup."
        )
        raise
    finally:
        progress.close()

    hide_progress = Progress(
        "Hiding names",
        total=len(entries) + len(dir_entries),
    )
    try:
        hide_entry_names(root, entries, dir_entries, progress=hide_progress)
    except Exception:
        print()
        print(
            "ERROR while hiding names. Files are encrypted; a manifest exists. "
            "Unlock with the same password to restore names, or restore from backup."
        )
        raise
    finally:
        hide_progress.close()

    print()
    print("Directory locked.")
    print(f"Files: {len(entries)}")
    if skipped:
        print(f"Skipped: {skipped}")
    print(f"Cipher: {cipher_name}")
    print("Names: encrypted")
    print(f"Manifest: {manifest_path(root)}")


def load_locked(
    root: Path,
    password: str,
    *,
    key_file: Path | None = None,
):
    root = root.resolve()
    path = manifest_path(root)

    if not path.is_file():
        raise ValueError(f"Directory is not locked (missing {path})")

    manifest = json.loads(path.read_text(encoding="utf-8"))
    version = manifest.get("version")

    if version not in SUPPORTED_VERSIONS:
        raise ValueError(f"Unsupported vault version: {version}")

    if manifest.get("mode") not in (None, "in-place"):
        raise ValueError(
            f"Unsupported lock mode: {manifest.get('mode')}"
        )

    uses_key_file = bool(manifest.get("kdf", {}).get("key_file"))
    if uses_key_file and key_file is None:
        raise ValueError(
            "This directory was locked with an optional key file. "
            "Provide the same key file."
        )

    salt = bytes.fromhex(manifest["kdf"]["salt"])
    algorithm = normalize_cipher(
        manifest.get("cipher", {}).get("algorithm", CIPHER_CHACHA)
    )
    spec = get_cipher_spec(algorithm)
    key = derive_key(
        password,
        salt,
        key_file=key_file if uses_key_file else None,
        key_size=spec.key_size,
        cipher_name=algorithm,
    )
    stored_nonce = manifest.get("cipher", {}).get("nonce_size")
    nonce_size = int(stored_nonce) if stored_nonce else spec.nonce_size
    cipher = make_cipher(algorithm, key, nonce_size=nonce_size)
    files, dirs = resolve_inventory(manifest, cipher)
    return root, manifest, cipher, files, dirs


def password_opens(
    root: Path,
    password: str,
    *,
    key_file: Path | None = None,
) -> bool:
    """Return True if password decrypts this locked directory's inventory."""
    if not password or not is_locked(root):
        return False
    try:
        load_locked(root, password, key_file=key_file)
    except Exception:
        return False
    return True


def verify_directory(
    root: Path,
    password: str,
    *,
    key_file: Path | None = None,
) -> None:
    root, _manifest, cipher, files, _dirs = load_locked(
        root,
        password,
        key_file=key_file,
    )

    print("Verifying locked directory...")
    print()

    from xenon.progress import Progress

    progress = Progress("Verifying", total=len(files))
    try:
        for entry in files:
            relative = entry["path"]
            progress.update(1, suffix=relative[:48])
            path = locate_entry_path(root, entry, want_file=True)
            blob = path.read_bytes()
            nonce, ciphertext = unpack_encrypted(blob, cipher.nonce_size)
            cipher.decrypt(nonce, ciphertext, relative.encode("utf-8"))
    finally:
        progress.close()

    print()
    print("Verification successful.")


def unlock_directory(
    root: Path,
    password: str,
    *,
    key_file: Path | None = None,
) -> None:
    root, _manifest, cipher, files, dirs = load_locked(
        root,
        password,
        key_file=key_file,
    )

    print(f"Unlocking in place: {root}")
    print()

    from xenon.progress import Progress

    progress = Progress("Decrypting", total=len(files))
    try:
        for entry in files:
            relative = entry["path"]
            progress.update(1, suffix=relative[:48])
            path = locate_entry_path(root, entry, want_file=True)
            decrypt_in_place(path, cipher, relative)
        restore_entry_names(root, files, dirs)
    finally:
        progress.close()

    path = manifest_path(root)
    path.unlink(missing_ok=True)

    meta = xenon_dir(root)
    try:
        next(meta.iterdir())
    except StopIteration:
        meta.rmdir()
    except FileNotFoundError:
        pass

    print()
    print("Directory unlocked.")


def generate_key_file(path: Path) -> Path:
    path = path.expanduser()
    if path.exists():
        raise FileExistsError(f"Key file already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(os.urandom(KEY_SIZE))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return path
