"""Capture production recovery artifacts on the Docker host, then verify offsite bytes.

Run as root. This captures backups; it does not retire SQL or restore live databases.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import traceback


APP_CONTAINER = "p6-backup-admin-web"
PG_CONTAINER = "p6-reporting-postgres"
CONTAINERS = (
    APP_CONTAINER, "p6-backup-admin-celery", "p6-backup-admin-celery-beat",
    "p6-reporting-worker", PG_CONTAINER, "p6-reporting-sql-stage",
    "p6-reporting-tcp", "AxialP6_2212", "AxialP6", "p6-backup-redis",
    "WebServer-Manager", "portainer",
)
APP_EXCLUDES = (
    "app/.recovery", "app/.git", "app/.venv", "app/work", "app/remote_edits",
    "app/backup-temp", "app/staticfiles", "app/db.sqlite3*", "app/__pycache__",
    "*/__pycache__", "*.pyc", "app/backups/.codex-live-backups",
    "app/backups/.codex-deploy-staging", "app/backups/.codex-live-staging",
    "app/.pytest_cache", "app/.ruff_cache",
)


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{5,95}", value):
        raise argparse.ArgumentTypeError("Recovery ID must be 6-96 safe identifier characters.")
    return value


def json_file(path: Path, payload: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, indent=2, sort_keys=True, default=str)
        stream.write("\n")


def run(command: list[str], log, *, output: Path | None = None) -> bytes:
    # Commands have no credential values; full diagnostics remain in a private file.
    log.write(("COMMAND " + json.dumps(command) + "\n").encode())
    log.flush()
    if output is not None:
        with output.open("xb") as target:
            subprocess.run(command, check=True, stdout=target, stderr=log)
        return b""
    result = subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=log)
    return result.stdout


def build_manifest(destination: Path, *, recovery_id: str, excludes: tuple[str, ...], containers: list[dict]) -> dict:
    files = []
    for path in sorted(destination.rglob("*")):
        if path.is_file() and path.name not in {"capture.log", "FAILED.json", "COMPLETE.json"}:
            files.append({"path": path.relative_to(destination).as_posix(), "size_bytes": path.stat().st_size, "sha256": checksum(path)})
    return {"schema_version": 1, "recovery_id": recovery_id, "captured_at": datetime.now(timezone.utc).isoformat(), "hostname": os.uname().nodename, "files": files, "app_source_excludes": list(excludes), "containers": [{"name": item["Name"].lstrip("/"), "image_id": item["Image"]} for item in containers], "verification": {"sqlite": "integrity_check in application/capture.json", "sql": "COPY_ONLY CHECKSUM and RESTORE VERIFYONLY in application/capture.json", "postgres": "pg_dump custom archive; isolated pg_restore required separately", "offsite": "upload/download/decrypt/checksum receipt recorded separately"}, "recovery_limitations": ["Capture timestamps differ across stores; quiesce reporting publication/settings changes if cross-store consistency is required.", "Docker volumes are recovered from logical SQL/PostgreSQL backups; proxy/Portainer bind data is included.", "This capture is not an isolated restore drill. Record that proof before retiring legacy SQL objects."]}


def capture(args, destination: Path, log) -> dict:
    base = args.deployment.resolve(strict=True)
    app = base / "app"
    helper = "/app/ops/recovery/application_recovery.py"
    in_container = f"/app/.recovery/{args.recovery_id}"
    guard_command = ["docker", "exec", APP_CONTAINER, "python", helper, "guard", "--destination", in_container, "--recovery-id", args.recovery_id]
    initial_state = json.loads(run(guard_command, log))
    json_file(destination / "reporting-state.json", initial_state)
    inspect = json.loads(run(["docker", "inspect", *CONTAINERS], log))
    json_file(destination / "docker-inspect.json", inspect)
    json_file(destination / "docker-networks.json", json.loads(run(["docker", "network", "inspect", *sorted({name for row in inspect for name in row.get("NetworkSettings", {}).get("Networks", {})})], log)))
    run(["docker", "version", "--format", "{{json .}}"], log, output=destination / "docker-version.json")
    print("Capturing application source and deployment configuration", flush=True)
    run(["tar", "--create", "--gzip", "--file", str(destination / "application-source.tar.gz"), "--directory", str(base), *[f"--exclude={pattern}" for pattern in APP_EXCLUDES], "app"], log)
    config_names = ["compose.json", "private", "state", "scripts", "proxy", "portainer-data", "reporting-secrets.json", "reporting-initial-state.json"]
    missing = [name for name in config_names if not (base / name).exists()]
    if missing:
        raise RuntimeError("Required deployment configuration is missing: " + ", ".join(missing))
    run(["tar", "--create", "--gzip", "--file", str(destination / "deployment-config.tar.gz"), "--directory", str(base), *config_names], log)
    print("Capturing consistent SQLite and verified SQL Server backups", flush=True)
    summary = json.loads(run(["docker", "exec", APP_CONTAINER, "python", helper, "capture", "--destination", in_container, "--recovery-id", args.recovery_id], log))
    print("Capturing PostgreSQL database and global roles", flush=True)
    run(["docker", "exec", "--user", "postgres", PG_CONTAINER, "sh", "-c", 'exec pg_dump --format=custom --username="$POSTGRES_USER" --dbname=p6_reporting'], log, output=destination / "p6_reporting.dump")
    run(["docker", "exec", "--user", "postgres", PG_CONTAINER, "sh", "-c", 'exec pg_dumpall --globals-only --username="$POSTGRES_USER"'], log, output=destination / "postgres-globals.sql")
    run(["docker", "exec", "--user", "postgres", PG_CONTAINER, "sh", "-c", 'exec pg_dump --schema-only --username="$POSTGRES_USER" --dbname=p6_reporting'], log, output=destination / "postgres-schema.sql")
    if json.loads(run(guard_command, log)) != initial_state:
        raise RuntimeError("Reporting publication or settings changed during capture; quiesce reporting and retry.")
    print("Saving the unique runtime Docker images", flush=True)
    images = sorted({row["Image"] for row in inspect})
    run(["docker", "image", "save", *images], log, output=destination / "docker-images.tar")
    manifest = build_manifest(destination, recovery_id=args.recovery_id, excludes=APP_EXCLUDES, containers=inspect)
    json_file(destination / "manifest.json", manifest)
    print("Packaging recovery payload and verifying archive contents", flush=True)
    with tarfile.open(destination / "recovery.tar.gz", "x:gz", compresslevel=1) as archive:
        for row in manifest["files"]:
            archive.add(destination / row["path"], arcname=row["path"], recursive=False)
        archive.add(destination / "manifest.json", arcname="manifest.json", recursive=False)
    with tarfile.open(destination / "recovery.tar.gz", "r:gz") as archive:
        for row in manifest["files"]:
            member = archive.extractfile(row["path"])
            if member is None:
                raise RuntimeError("Recovery archive member is missing.")
            digest = hashlib.sha256()
            for chunk in iter(lambda: member.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
            if digest.hexdigest() != row["sha256"]:
                raise RuntimeError("Recovery archive content hash mismatch.")
    print("Encrypting and verifying the offsite recovery copy", flush=True)
    receipt = json.loads(run(["docker", "exec", APP_CONTAINER, "python", helper, "publish", "--destination", in_container, "--recovery-id", args.recovery_id], log))
    return {"recovery_id": args.recovery_id, "completed_at": datetime.now(timezone.utc).isoformat(), "capture": summary, "offsite": receipt, "manifest_sha256": checksum(destination / "manifest.json"), "isolated_restore_verified": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", type=Path, default=Path("/home/mat/Docker/P6-Standby"))
    parser.add_argument("--recovery-id", type=validate_id, default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Run this host capture as root (sudo -n python3 ...).")
    os.umask(0o077)
    parent = args.deployment.resolve(strict=True) / "app" / ".recovery"
    parent.mkdir(mode=0o700, exist_ok=True)
    parent.chmod(0o700)
    destination = parent / args.recovery_id
    destination.mkdir(mode=0o700)  # No reuse or overwrite, including a failed capture.
    with (destination / "capture.log").open("xb") as log:
        try:
            available = shutil.disk_usage(destination).free
            if available < 25 * 1024 ** 3:
                raise RuntimeError("At least 25 GiB free is required before recovery capture.")
            result = capture(args, destination, log)
            json_file(destination / "COMPLETE.json", result)
            print(json.dumps({"status": "complete", "directory": str(destination), "recovery_id": args.recovery_id, "offsite_verified": True, "isolated_restore_verified": False}), flush=True)
            return 0
        except BaseException as error:
            log.write(traceback.format_exc().encode())
            json_file(destination / "FAILED.json", {"failed_at": datetime.now(timezone.utc).isoformat(), "error_type": type(error).__name__, "diagnostic_path": str(destination / "capture.log"), "recovery_id": args.recovery_id})
            print(json.dumps({"status": "failed", "directory": str(destination), "diagnostic": "capture.log", "error_type": type(error).__name__}), file=sys.stderr, flush=True)
            return 1


if __name__ == "__main__":
    raise SystemExit(main())
