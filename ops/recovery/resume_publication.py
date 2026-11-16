"""Resume publication of an immutable completed recovery archive inside Django.

This creates no database backups and never replaces an archive or remote file.
Signed upload-session URLs remain in root-private recovery state and diagnostics.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import traceback
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from application_recovery import setup_django, validate_id, write_json


CHUNK_BYTES = 10 * 1024 * 1024  # Graph's required 320-KiB alignment.
SMALL_UPLOAD_LIMIT_BYTES = 250 * 1024 * 1024


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def reject_active_publisher(destination: Path, recovery_id: str) -> None:
    for process in Path("/proc").iterdir():
        if not process.name.isdigit() or int(process.name) == os.getpid():
            continue
        try:
            arguments = (process / "cmdline").read_bytes().decode().split("\0")
        except (FileNotFoundError, PermissionError, ProcessLookupError, UnicodeError):
            continue
        if any(Path(value).name == "application_recovery.py" for value in arguments) and "publish" in arguments and str(destination) in arguments and recovery_id in arguments:
            raise RuntimeError("The original recovery publisher is still running; wait for it to finish.")


def validate_artifacts(destination: Path, recovery_id: str) -> dict:
    names = ("manifest.json", "encryption_manifest.json", "recovery.tar.gz", "recovery.tar.gz.enc")
    for name in names:
        path = destination / name
        if path.is_symlink() or not path.is_file():
            raise RuntimeError("A required immutable recovery artifact is absent or unsafe.")
    manifest_path = destination / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    encryption = json.loads((destination / "encryption_manifest.json").read_text())
    if manifest.get("recovery_id") != recovery_id or encryption.get("recovery_id") != recovery_id:
        raise RuntimeError("Recovery artifact identities do not match the requested ID.")
    if encryption.get("capture_manifest_sha256") != sha256(manifest_path):
        raise RuntimeError("The immutable capture manifest checksum does not match.")
    for name, prefix in (("recovery.tar.gz", "archive"), ("recovery.tar.gz.enc", "encrypted")):
        path = destination / name
        if path.stat().st_size != encryption.get(prefix + "_bytes") or sha256(path) != encryption.get(prefix + "_sha256"):
            raise RuntimeError("An immutable recovery archive size or checksum does not match.")
    expected = {}
    for entry in manifest["files"]:
        name = entry["path"]
        if name in expected or name.startswith("/") or ".." in Path(name).parts:
            raise RuntimeError("Unsafe or duplicate capture manifest member.")
        expected[name] = entry
    expected["manifest.json"] = {"size_bytes": manifest_path.stat().st_size, "sha256": sha256(manifest_path)}
    seen = set()
    with tarfile.open(destination / "recovery.tar.gz", "r|gz") as archive:
        for member in archive:
            if not member.isfile() or member.name not in expected or member.name in seen:
                raise RuntimeError("Unexpected or duplicate immutable archive member.")
            reference = expected[member.name]
            if member.size != reference["size_bytes"]:
                raise RuntimeError("Immutable archive member size differs from its manifest.")
            source = archive.extractfile(member)
            digest = hashlib.sha256()
            for chunk in iter(lambda: source.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
            if digest.hexdigest() != reference["sha256"]:
                raise RuntimeError("Immutable archive member checksum differs from its manifest.")
            seen.add(member.name)
    if seen != set(expected):
        raise RuntimeError("Immutable recovery archive is incomplete.")
    return encryption


def save_transfer_state(path: Path, value: dict) -> None:
    # Only this mutable transfer sidecar is replaced, never capture artifacts.
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, path)


def item_at(client, name: str) -> dict | None:
    path = quote(f"{client.folder_path}/{name}", safe="/")
    response = client.session.get(f"https://graph.microsoft.com/v1.0/sites/{client.site_id}/drive/root:/{path}", headers=client.headers, timeout=30)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def resumable_upload(client, source: Path, expected_hash: str, state_path: Path) -> dict:
    """Create with fail-on-conflict, or continue this exact archive's session."""
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    identity = {"folder": client.folder_path, "filename": source.name, "sha256": expected_hash, "size_bytes": source.stat().st_size}
    if state and any(state.get(key) != value for key, value in identity.items()):
        raise RuntimeError("Persisted transfer state belongs to different recovery bytes.")
    if source.stat().st_size <= SMALL_UPLOAD_LIMIT_BYTES:
        existing = item_at(client, source.name)
        if existing is not None:
            return existing
        with source.open("rb") as stream:
            response = client.session.put(client._content_url(source.name), headers={**client.headers, "Content-Type": "application/octet-stream", "If-None-Match": "*"}, data=stream, timeout=300)
        if response.status_code in (409, 412):
            existing = item_at(client, source.name)
            if existing is not None:
                return existing  # Caller must still download and verify these bytes.
        response.raise_for_status()
        return response.json()
    upload_url = state.get("upload_url")
    progress = None
    if upload_url:
        response = client.session.get(upload_url, timeout=60)
        if response.status_code not in (404, 410):
            response.raise_for_status()
            progress = response.json()
        else:
            upload_url = None
    if not upload_url:
        # A completed item may have appeared after an interrupted final response.
        existing = item_at(client, source.name)
        if existing is not None:
            return existing
        response = client.session.post(client._upload_session_url(source.name), headers={**client.headers, "Content-Type": "application/json"}, json={"item": {"name": source.name, "@microsoft.graph.conflictBehavior": "fail"}}, timeout=30)
        response.raise_for_status()
        progress = response.json()
        upload_url = str(progress["uploadUrl"])
        state = {**identity, "upload_url": upload_url, "expiration": progress.get("expirationDateTime"), "next_expected_ranges": progress.get("nextExpectedRanges", ["0-"])}
        save_transfer_state(state_path, state)
    with source.open("rb") as stream:
        while True:
            ranges = progress.get("nextExpectedRanges", state.get("next_expected_ranges", ["0-"]))
            if not ranges:
                raise RuntimeError("Upload session returned no missing ranges or completed item.")
            current = min(ranges, key=lambda value: int(value.split("-", 1)[0]))
            start_text, end_text = current.split("-", 1)
            start = int(start_text)
            if not 0 <= start < source.stat().st_size:
                raise RuntimeError("Upload session returned an invalid next byte offset.")
            length = min(CHUNK_BYTES, source.stat().st_size - start)
            if end_text:
                length = min(length, int(end_text) - start + 1)
            if length <= 0 or (start + length < source.stat().st_size and length % (320 * 1024)):
                raise RuntimeError("Upload session returned an invalid or unaligned missing range.")
            stream.seek(start)
            chunk = stream.read(length)
            if len(chunk) != length:
                raise RuntimeError("The immutable encrypted archive changed during resumed transfer.")
            response = client.session.put(upload_url, headers={"Content-Length": str(len(chunk)), "Content-Range": f"bytes {start}-{start + len(chunk) - 1}/{source.stat().st_size}"}, data=chunk, timeout=300)
            response.raise_for_status()
            progress = response.json()
            if response.status_code in (200, 201):
                save_transfer_state(state_path, {**identity, "status": "uploaded", "item_id": progress.get("id")})
                return progress
            if response.status_code != 202:
                raise RuntimeError("Unexpected recovery upload-session response.")
            state["next_expected_ranges"] = progress["nextExpectedRanges"]
            save_transfer_state(state_path, state)


def verify_remote(client, source: Path, expected_hash: str, destination: Path, name: str) -> dict:
    from backups.services.sharepoint import download_file
    item = item_at(client, name)
    if item is None or "file" not in item or item.get("size") != source.stat().st_size:
        raise RuntimeError("The remote recovery artifact is absent or has a different size.")
    download_url = str(item.get("@microsoft.graph.downloadUrl", ""))
    if not download_url:
        raise RuntimeError("The remote recovery artifact has no download URL.")
    download_file(download_url, destination)
    if sha256(destination) != expected_hash:
        raise RuntimeError("The remote recovery artifact checksum differs; it was not overwritten.")
    return item


def remote_identity(item: dict | None) -> dict | None:
    """All server validators are required before a downloaded proof is reusable."""
    if not item or "file" not in item or not all(item.get(key) for key in ("id", "eTag", "cTag", "size")):
        return None
    return {key: item[key] for key in ("id", "eTag", "cTag", "size")}


def archive_proof(item: dict, encryption: dict, recovery_id: str) -> dict:
    return {"schema_version": 1, "recovery_id": recovery_id, "remote_identity": remote_identity(item), "encrypted_sha256": encryption["encrypted_sha256"], "archive_sha256": encryption["archive_sha256"], "capture_manifest_sha256": encryption["capture_manifest_sha256"], "key_sha256": encryption["key_sha256"], "upload_download_decrypt_sha256": "passed", "verified_at": datetime.now(timezone.utc).isoformat()}


def archive_proof_matches(proof: dict, item: dict | None, encryption: dict, recovery_id: str) -> bool:
    identity = remote_identity(item)
    if not identity or identity["size"] != encryption["encrypted_bytes"]:
        return False
    expected = archive_proof(item, encryption, recovery_id)
    return all(proof.get(key) == value for key, value in expected.items() if key != "verified_at") and bool(proof.get("verified_at"))


def resume(destination: Path, recovery_id: str) -> dict:
    from reporting_mirror.models import ReportingSource
    from backups.services.security import decrypt_file_chunked, get_encryption_keys
    from backups.services.sharepoint import build_sharepoint_client

    reject_active_publisher(destination, recovery_id)
    encryption = validate_artifacts(destination, recovery_id)
    keys = get_encryption_keys()
    if encryption["key_sha256"] not in {hashlib.sha256(key.encode()).hexdigest() for key in keys}:
        raise RuntimeError("The archived recovery encryption key is absent from the available keyring.")
    source = ReportingSource.objects.select_related("target").get(code="p6_22")
    folder = f"AxialP6_Backups/Recovery/{recovery_id}"
    client = build_sharepoint_client(sharepoint_site=source.target.sharepoint_site, sharepoint_folder=folder)
    names = {"recovery.tar.gz.enc", "encryption_manifest.json",
             "retirement-evidence.tar.gz.enc", "retirement-evidence.manifest.json"}
    if any(item.get("name") not in names for item in client.list_folder_children()):
        raise RuntimeError("The existing recovery folder contains unexpected files.")
    reject_active_publisher(destination, recovery_id)
    encrypted = destination / "recovery.tar.gz.enc"
    if item_at(client, encrypted.name) is None:
        resumable_upload(client, encrypted, encryption["encrypted_sha256"], destination / ".resume-upload-state.json")
    proof_path = destination / ".archive-remote-verification.json"
    try:
        cached_proof = json.loads(proof_path.read_text()) if not proof_path.is_symlink() else {}
    except (OSError, ValueError):
        cached_proof = {}
    item = item_at(client, encrypted.name)
    reused_archive_proof = archive_proof_matches(cached_proof, item, encryption, recovery_id)
    with tempfile.TemporaryDirectory(prefix="resume-proof-", dir=destination) as temporary:
        root = Path(temporary)
        if not reused_archive_proof:
            encrypted_copy = root / "remote.enc"
            item = verify_remote(client, encrypted, encryption["encrypted_sha256"], encrypted_copy, encrypted.name)
            restored = root / "restored.tar.gz"
            decrypt_file_chunked(encrypted_copy, restored, keys)
            if sha256(restored) != encryption["archive_sha256"]:
                raise RuntimeError("The resumed offsite archive plaintext checksum did not match.")
            # Persist immediately, before metadata publication can fail.
            save_transfer_state(proof_path, archive_proof(item, encryption, recovery_id))
        metadata = destination / "encryption_manifest.json"
        if item_at(client, metadata.name) is None:
            resumable_upload(client, metadata, sha256(metadata), destination / ".resume-metadata-state.json")
        metadata_item = verify_remote(client, metadata, sha256(metadata), root / "metadata.json", metadata.name)
    if remote_identity(item) is not None and remote_identity(item_at(client, encrypted.name)) != remote_identity(item):
        raise RuntimeError("The remote archive changed after its verification; no completion receipt was issued.")
    receipt = {"recovery_id": recovery_id, "folder": folder, "archive_item_id": str(item.get("id", "")), "archive_web_url": str(item.get("webUrl", "")), "metadata_item_id": str(metadata_item.get("id", "")), "encrypted_sha256": encryption["encrypted_sha256"], "archive_sha256": encryption["archive_sha256"], "upload_download_decrypt_sha256": "passed", "verified_at": datetime.now(timezone.utc).isoformat(), "publication_resumed": True, "archive_verification_reused": reused_archive_proof}
    canonical = destination / "offsite_receipt.json"
    if canonical.exists():
        prior = json.loads(canonical.read_text())
        for key in ("recovery_id", "encrypted_sha256", "archive_sha256", "archive_item_id", "metadata_item_id"):
            if prior.get(key) != receipt.get(key):
                raise RuntimeError("An existing offsite receipt identifies different recovery artifacts.")
    else:
        write_json(canonical, receipt)
    write_json(destination / f"offsite_resume_receipt-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.json", receipt)
    return receipt


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--recovery-id", type=validate_id, required=True)
    args = parser.parse_args()
    destination = args.destination.resolve(strict=True)
    if destination.name != args.recovery_id or destination.parent.name != ".recovery":
        parser.error("Destination must be an existing .recovery/<recovery-id> directory.")
    attempt = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    with (destination / ".resume-publication.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            setup_django()
            receipt = resume(destination, args.recovery_id)
            print(json.dumps({"status": "offsite_verified", **receipt}, sort_keys=True))
            return 0
        except Exception as error:
            with (destination / f"resume-publication-{attempt}.log").open("x") as diagnostic:
                traceback.print_exc(file=diagnostic)
            print(json.dumps({"status": "failed", "error_type": type(error).__name__, "diagnostic": f"resume-publication-{attempt}.log"}), file=sys.stderr)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
