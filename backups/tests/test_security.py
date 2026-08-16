from __future__ import annotations

import tempfile
from pathlib import Path

from cryptography.fernet import Fernet
from django.test import SimpleTestCase

from backups.services.security import calculate_file_sha256, decrypt_file_chunked, encrypt_file_chunked


class SecurityTests(SimpleTestCase):
    def test_chunked_fernet_round_trip_preserves_content_and_hash(self):
        key = Fernet.generate_key().decode("utf-8")
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "source.bak"
            encrypted = root / "source.bak.enc"
            restored = root / "restored.bak"
            source.write_bytes((b"abc123" * 1024) + b"tail")

            source_hash = calculate_file_sha256(source)
            encrypt_file_chunked(source, encrypted, key, chunk_size=128)
            decrypt_file_chunked(encrypted, restored, [key])

            self.assertNotEqual(source.read_bytes(), encrypted.read_bytes())
            self.assertEqual(source_hash, calculate_file_sha256(restored))
            self.assertEqual(source.read_bytes(), restored.read_bytes())
