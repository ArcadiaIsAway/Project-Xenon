from pathlib import Path

import pytest

from xenon.exclusions import ExclusionPolicy
from xenon.probe import probe_scope
from xenon.vault import (
    CIPHER_AESGCM,
    CIPHER_CHACHA,
    SUPPORTED_CIPHERS,
    XChaCha20Poly1305,
    _hchacha20,
    check_password_verifier,
    generate_key_file,
    is_destroyed_vault,
    is_locked,
    is_passwordless_vault,
    is_read_protect_vault,
    lock_directory,
    make_password_verifier,
    normalize_cipher,
    password_opens,
    unlock_directory,
    verify_directory,
)
from xenon.walk import iter_files


def test_lock_unlock_roundtrip(tmp_path: Path):
    sample = tmp_path / "data"
    nested = sample / "nested"
    nested.mkdir(parents=True)
    (sample / "a.txt").write_text("alpha", encoding="utf-8")
    (nested / "b.txt").write_text("beta", encoding="utf-8")

    password = "unit-test-password"
    lock_directory(sample, password)

    assert is_locked(sample)
    manifest = (sample / ".xenon" / "manifest.json").read_text(encoding="utf-8")
    assert "inventory" in manifest
    assert "a.txt" not in manifest
    assert "sha256" not in manifest
    assert not (sample / "a.txt").exists()
    assert not (nested / "b.txt").exists()
    visible = {
        path.name
        for path in sample.rglob("*")
        if ".xenon" not in path.parts
    }
    assert "a.txt" not in visible
    assert "nested" not in visible
    ciphertext_files = [
        path for path in sample.rglob("*")
        if path.is_file() and ".xenon" not in path.parts
    ]
    assert ciphertext_files
    assert all(path.read_bytes().startswith(b"XENON") for path in ciphertext_files)
    assert all(path.name.startswith("xenon-") for path in ciphertext_files)

    verify_directory(sample, password)
    unlock_directory(sample, password)

    assert not is_locked(sample)
    assert (sample / "a.txt").read_text(encoding="utf-8") == "alpha"
    assert (nested / "b.txt").read_text(encoding="utf-8") == "beta"


def test_aes_gcm_with_key_file(tmp_path: Path):
    sample = tmp_path / "data"
    sample.mkdir()
    (sample / "note.txt").write_text("secret", encoding="utf-8")
    key_path = tmp_path / "xenon.key"
    generate_key_file(key_path)

    password = "unit-test-password"
    lock_directory(
        sample,
        password,
        cipher_name=CIPHER_AESGCM,
        key_file=key_path,
    )
    verify_directory(sample, password, key_file=key_path)
    unlock_directory(sample, password, key_file=key_path)
    assert (sample / "note.txt").read_text(encoding="utf-8") == "secret"


@pytest.mark.parametrize("cipher_name", SUPPORTED_CIPHERS)
def test_lock_unlock_every_cipher(tmp_path: Path, cipher_name: str):
    sample = tmp_path / "data"
    nested = sample / "nested"
    nested.mkdir(parents=True)
    (sample / "a.txt").write_text("alpha", encoding="utf-8")
    (nested / "b.txt").write_text("beta", encoding="utf-8")

    password = "unit-test-password"
    lock_directory(sample, password, cipher_name=cipher_name)

    manifest = (sample / ".xenon" / "manifest.json").read_text(encoding="utf-8")
    assert cipher_name in manifest
    assert not (sample / "a.txt").exists()
    assert not (nested / "b.txt").exists()

    verify_directory(sample, password)
    unlock_directory(sample, password)
    assert (sample / "a.txt").read_text(encoding="utf-8") == "alpha"
    assert (nested / "b.txt").read_text(encoding="utf-8") == "beta"


def test_normalize_cipher_aliases():
    assert normalize_cipher(None) == CIPHER_CHACHA
    assert normalize_cipher("chacha") == CIPHER_CHACHA
    assert normalize_cipher("aes") == CIPHER_AESGCM
    assert normalize_cipher("xchacha") == "XChaCha20-Poly1305"
    assert normalize_cipher("aes-128-gcm") == "AES-128-GCM"
    assert normalize_cipher("gcm-siv") == "AES-256-GCM-SIV"
    assert normalize_cipher("siv") == "AES-256-SIV"
    assert normalize_cipher("ocb3") == "AES-256-OCB3"
    with pytest.raises(ValueError, match="Unsupported cipher"):
        normalize_cipher("twofish")


def test_hchacha20_ietf_vector():
    key = bytes(range(32))
    nonce = bytes.fromhex("000000090000004a0000000031415927")
    expected = bytes.fromhex(
        "82413b4227b27bfed30e42508a877d73a0f9e4d58a74a853c12ec41326d3ecdc"
    )
    assert _hchacha20(key, nonce) == expected


def test_xchacha20_poly1305_ietf_vector():
    key = bytes.fromhex(
        "808182838485868788898a8b8c8d8e8f909192939495969798999a9b9c9d9e9f"
    )
    nonce = bytes.fromhex("404142434445464748494a4b4c4d4e4f5051525354555657")
    aad = bytes.fromhex("50515253c0c1c2c3c4c5c6c7")
    plaintext = (
        b"Ladies and Gentlemen of the class of '99: If I could offer you "
        b"only one tip for the future, sunscreen would be it."
    )
    expected = bytes.fromhex(
        "bd6d179d3e83d43b9576579493c0e939572a1700252bfaccbed2902c21396cbb"
        "731c7f1b0b4aa6440bf3a82f4eda7e39ae64c6708c54c216cb96b72e1213b452"
        "2f8c9ba40db5d945b11b69b982c1bb9e3f3fac2bc369488f76b2383565d3fff9"
        "21f9664c97637da9768812f615c68b13b52e"
        "c0875924c1c7987947deafd8780acf49"
    )
    aead = XChaCha20Poly1305(key)
    assert aead.encrypt(nonce, plaintext, aad) == expected
    assert aead.decrypt(nonce, expected, aad) == plaintext


def test_probe_scope_ok(tmp_path: Path):
    sample = tmp_path / "data"
    sample.mkdir()
    (sample / "ok.txt").write_text("hello", encoding="utf-8")
    report = probe_scope(sample, show_progress=False)
    assert report.passed
    assert report.ok_count == 1
    assert report.fail_count == 0


def test_probe_scope_unwritable_parent(tmp_path: Path):
    sample = tmp_path / "data"
    nested = sample / "nested"
    nested.mkdir(parents=True)
    target = nested / "file.txt"
    target.write_text("hello", encoding="utf-8")
    nested.chmod(0o555)
    sample.chmod(0o555)
    try:
        report = probe_scope(sample, show_progress=False)
        assert report.fail_count >= 1 or report.root_issues
    finally:
        sample.chmod(0o755)
        nested.chmod(0o755)


def test_walk_skips_cache_dirs(tmp_path: Path):
    sample = tmp_path / "data"
    cache = sample / ".cache" / "spotify"
    cache.mkdir(parents=True)
    (cache / "blob").write_text("nope", encoding="utf-8")
    (sample / "keep.txt").write_text("yes", encoding="utf-8")
    policy = ExclusionPolicy()
    found = {p.name for p in iter_files(sample, policy=policy)}
    assert found == {"keep.txt"}


def test_custom_exclusion_and_toggle(tmp_path: Path):
    sample = tmp_path / "data"
    keep = sample / "keep"
    skip = sample / "spotify"
    keep.mkdir(parents=True)
    skip.mkdir(parents=True)
    (keep / "a.txt").write_text("a", encoding="utf-8")
    (skip / "b.txt").write_text("b", encoding="utf-8")

    policy = ExclusionPolicy(
        optional_dirs={".cache": False},
        custom_paths=[str(skip)],
    )
    found = {p.relative_to(sample).as_posix() for p in iter_files(sample, policy=policy)}
    assert found == {"keep/a.txt"}


def test_lock_encrypts_file_and_folder_names(tmp_path: Path):
    sample = tmp_path / "data"
    photos = sample / "Photos" / "Vacation"
    photos.mkdir(parents=True)
    (photos / "img.jpg").write_text("pic", encoding="utf-8")
    (sample / "secret notes.txt").write_text("classified", encoding="utf-8")
    empty = sample / "Empty Folder"
    empty.mkdir()
    unicode_dir = sample / "文档"
    unicode_dir.mkdir()
    (unicode_dir / "résumé.pdf").write_text("cv", encoding="utf-8")

    lock_directory(sample, "unit-test-password")

    leftover = {
        path.name
        for path in sample.rglob("*")
        if ".xenon" not in path.parts
    }
    for plaintext in (
        "Photos",
        "Vacation",
        "img.jpg",
        "secret notes.txt",
        "Empty Folder",
        "文档",
        "résumé.pdf",
    ):
        assert plaintext not in leftover

    assert leftover
    assert all(name.startswith("xenon-") for name in leftover)

    verify_directory(sample, "unit-test-password")
    unlock_directory(sample, "unit-test-password")

    assert (photos / "img.jpg").read_text(encoding="utf-8") == "pic"
    assert (sample / "secret notes.txt").read_text(encoding="utf-8") == "classified"
    assert empty.is_dir()
    assert (unicode_dir / "résumé.pdf").read_text(encoding="utf-8") == "cv"


def test_excluded_folder_names_are_left_visible(tmp_path: Path):
    sample = tmp_path / "data"
    keep = sample / "keep"
    skip = sample / "spotify"
    keep.mkdir(parents=True)
    skip.mkdir(parents=True)
    (keep / "a.txt").write_text("a", encoding="utf-8")
    (skip / "b.txt").write_text("b", encoding="utf-8")

    policy = ExclusionPolicy(custom_paths=[str(skip)])
    lock_directory(sample, "unit-test-password", policy=policy)

    assert skip.is_dir()
    assert (skip / "b.txt").read_text(encoding="utf-8") == "b"
    assert not (keep / "a.txt").exists()

    unlock_directory(sample, "unit-test-password")
    assert (keep / "a.txt").read_text(encoding="utf-8") == "a"
    assert (skip / "b.txt").read_text(encoding="utf-8") == "b"


def test_password_opens_rejects_wrong_secret(tmp_path: Path):
    sample = tmp_path / "data"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
    lock_directory(sample, "correct-horse")
    assert password_opens(sample, "correct-horse")
    assert not password_opens(sample, "wrong-battery")
    assert not password_opens(sample, "")
    unlock_directory(sample, "correct-horse")
    assert not password_opens(sample, "correct-horse")


def test_passwordless_lock_unlock(tmp_path: Path):
    sample = tmp_path / "data"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
    lock_directory(sample, "", aggressiveness=2)
    assert is_passwordless_vault(sample)
    assert password_opens(sample, "")
    verify_directory(sample, "")
    unlock_directory(sample, "")
    assert not is_locked(sample)
    assert (sample / "a.txt").read_text(encoding="utf-8") == "hello"


def test_password_verifier_roundtrip():
    blob = make_password_verifier("correct-horse")
    assert "correct-horse" not in str(blob)
    assert check_password_verifier("correct-horse", blob)
    assert not check_password_verifier("wrong-battery", blob)
    assert not check_password_verifier("", blob)
    with pytest.raises(ValueError, match="empty"):
        make_password_verifier("")


def test_project_protection(tmp_path: Path):
    policy = ExclusionPolicy()
    project_file = Path(__file__).resolve()
    reason = policy.is_protected_path(project_file)
    assert reason is not None
    assert reason.startswith("protected")


def test_targets_migrate_from_legacy_source():
    from xenon.config import merge_config, targets_from_config

    merged = merge_config({"source": "~/Documents"})
    assert "source" not in merged
    assert targets_from_config(merged) == ["~/Documents"]

    multi = merge_config(
        {
            "targets": ["~/a", "~/b", "~/a"],
            "source": "~/ignored-when-targets-set",
        }
    )
    assert targets_from_config(multi) == ["~/a", "~/b"]


def test_read_protect_roundtrip(tmp_path: Path):
    sample = tmp_path / "data"
    sample.mkdir()
    target = sample / "secret.txt"
    target.write_text("classified", encoding="utf-8")
    original_mode = target.stat().st_mode & 0o777

    lock_directory(sample, "correct-horse", aggressiveness=1)
    assert is_read_protect_vault(sample)
    assert target.is_file()
    assert target.stat().st_mode & 0o777 == 0
    with pytest.raises(PermissionError):
        target.read_text(encoding="utf-8")

    unlock_directory(sample, "correct-horse")
    assert not is_locked(sample)
    assert target.read_text(encoding="utf-8") == "classified"
    assert target.stat().st_mode & 0o777 == original_mode


def test_delete_without_overwrite(tmp_path: Path):
    sample = tmp_path / "data"
    nested = sample / "Photos" / "Vacation"
    nested.mkdir(parents=True)
    target = nested / "secret.txt"
    target.write_text("classified", encoding="utf-8")
    empty = sample / "Empty Folder"
    empty.mkdir()

    lock_directory(sample, "", aggressiveness=4)
    assert not target.exists()
    assert not nested.exists()
    assert not (sample / "Photos").exists()
    assert not empty.exists()
    assert not sample.exists()
    assert not is_destroyed_vault(sample)


def test_secure_destruction(tmp_path: Path):
    sample = tmp_path / "data"
    nested = sample / "keep" / "inner"
    nested.mkdir(parents=True)
    target = nested / "secret.txt"
    target.write_text("classified", encoding="utf-8")

    lock_directory(sample, "correct-horse", aggressiveness=5)
    assert not target.exists()
    assert not nested.exists()
    assert not (sample / "keep").exists()
    assert not sample.exists()
    assert not is_destroyed_vault(sample)


def test_standard_encryption_rejects_empty_password(tmp_path: Path):
    sample = tmp_path / "data"
    sample.mkdir()
    (sample / "a.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(ValueError, match="requires a password"):
        lock_directory(sample, "", aggressiveness=3)
