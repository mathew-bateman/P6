from __future__ import annotations

import hashlib
import struct
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings


MAGIC = b"P6BAKFERNET1\n"
DEFAULT_CHUNK_SIZE = 4 * 1024 * 1024


def calculate_file_sha256(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def get_encryption_keys() -> list[str]:
    keys = [str(key).strip() for key in getattr(settings, "BACKUP_ENCRYPTION_KEYS", []) if str(key).strip()]
    if not keys:
        raise RuntimeError("BACKUP_ENCRYPTION_KEYS must contain at least one Fernet key.")
    for key in keys:
        Fernet(key.encode("utf-8"))
    return keys


def encrypt_file_chunked(
    input_path: Path,
    output_path: Path,
    encryption_key: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> None:
    cipher = Fernet(encryption_key.encode("utf-8"))
    with input_path.open("rb") as source, output_path.open("wb") as destination:
        destination.write(MAGIC)
        while True:
            chunk = source.read(chunk_size)
            if not chunk:
                break
            token = cipher.encrypt(chunk)
            destination.write(struct.pack(">Q", len(token)))
            destination.write(token)


def decrypt_file_chunked(
    input_path: Path,
    output_path: Path,
    encryption_keys: list[str] | str,
) -> None:
    if isinstance(encryption_keys, str):
        encryption_keys = [encryption_keys]

    ciphers = MultiFernet([Fernet(key.encode("utf-8")) for key in encryption_keys])
    with input_path.open("rb") as source:
        magic = source.read(len(MAGIC))
        if magic != MAGIC:
            source.seek(0)
            try:
                output_path.write_bytes(ciphers.decrypt(source.read()))
            except InvalidToken as error:
                raise ValueError("Backup decryption failed. No configured key matched.") from error
            return

        with output_path.open("wb") as destination:
            while True:
                header = source.read(8)
                if not header:
                    break
                if len(header) != 8:
                    raise ValueError("Encrypted backup is truncated.")
                token_length = struct.unpack(">Q", header)[0]
                token = source.read(token_length)
                if len(token) != token_length:
                    raise ValueError("Encrypted backup is truncated.")
                try:
                    destination.write(ciphers.decrypt(token))
                except InvalidToken as error:
                    raise ValueError("Backup decryption failed. No configured key matched.") from error
