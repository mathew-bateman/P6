from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests

from graph.services.tokens import get_app_token


SMALL_UPLOAD_LIMIT_BYTES = 250 * 1024 * 1024
DEFAULT_UPLOAD_CHUNK_SIZE = 10 * 1024 * 1024


def _quote_drive_path(path: str) -> str:
    return quote(path.strip("/"), safe="/")


def normalise_sharepoint_site_identifier(site_identifier: str) -> str:
    value = (site_identifier or "").strip()
    if not value:
        return "root"
    if value == "root":
        return value
    if ":/" in value and not value.endswith(":"):
        return f"{value}:"
    return value


def parse_sharepoint_url(url: str, *, default_folder: str = "P6 Backups") -> dict[str, str]:
    value = unquote((url or "").strip())
    if not value:
        return {"site": "root", "folder": default_folder}
    if not value.startswith(("http://", "https://")):
        return {"site": "root", "folder": value.strip("/") or default_folder}

    parsed = urlparse(value)
    path = unquote(parsed.path)
    query = parse_qs(parsed.query)
    if "id" in query:
        path = unquote(query["id"][0])
    if "Forms/AllItems.aspx" in path:
        path = path.replace("Forms/AllItems.aspx", "")

    site_match = re.search(r"^/(sites|teams)/([^/]+)", path)
    if site_match:
        site_root = site_match.group(0)
        remaining_path = path[len(site_root):].lstrip("/")
    else:
        site_root = ""
        remaining_path = path.lstrip("/")

    parts = remaining_path.split("/", 1)
    if parts and ("document" in parts[0].lower() or "shared" in parts[0].lower()):
        folder = parts[1] if len(parts) > 1 else ""
    else:
        folder = remaining_path

    site = f"{parsed.netloc}:{site_root}:" if site_root else parsed.netloc
    return {"site": normalise_sharepoint_site_identifier(site), "folder": folder.strip("/") or default_folder}


def resolve_sharepoint_site_id(token: str, configured_site: str) -> str:
    site_id = normalise_sharepoint_site_identifier(configured_site)
    if site_id == "root":
        return site_id
    response = requests.get(
        f"https://graph.microsoft.com/v1.0/sites/{site_id}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        timeout=30,
    )
    response.raise_for_status()
    return str(response.json().get("id", site_id))


def download_file(url: str, destination: Path) -> None:
    response = requests.get(url, stream=True, timeout=300)
    response.raise_for_status()
    with destination.open("wb") as output:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                output.write(chunk)


class SharePointClient:
    def __init__(self, *, token: str, site_id: str, folder_path: str, session=None) -> None:
        self.token = token
        self.site_id = site_id
        self.folder_path = folder_path.strip("/")
        self.session = session or requests.Session()
        self._drive_id: str | None = None

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}

    def drive_id(self) -> str:
        if self._drive_id is not None:
            return self._drive_id
        response = self.session.get(
            f"https://graph.microsoft.com/v1.0/sites/{self.site_id}/drive",
            headers=self.headers,
            timeout=30,
        )
        response.raise_for_status()
        self._drive_id = str(response.json()["id"])
        return self._drive_id

    def _content_url(self, remote_filename: str) -> str:
        drive_path = _quote_drive_path(f"{self.folder_path}/{remote_filename}")
        return f"https://graph.microsoft.com/v1.0/sites/{self.site_id}/drive/root:/{drive_path}:/content"

    def _upload_session_url(self, remote_filename: str) -> str:
        drive_path = _quote_drive_path(f"{self.folder_path}/{remote_filename}")
        return (
            f"https://graph.microsoft.com/v1.0/sites/{self.site_id}/drive/root:"
            f"/{drive_path}:/createUploadSession"
        )

    def upload_file(
        self,
        local_file_path: Path,
        remote_filename: str,
        *,
        chunk_size: int = DEFAULT_UPLOAD_CHUNK_SIZE,
    ) -> dict[str, Any]:
        if local_file_path.stat().st_size <= SMALL_UPLOAD_LIMIT_BYTES:
            return self._upload_small_file(local_file_path, remote_filename)
        return self._upload_large_file(local_file_path, remote_filename, chunk_size=chunk_size)

    def _upload_small_file(self, local_file_path: Path, remote_filename: str) -> dict[str, Any]:
        with local_file_path.open("rb") as source:
            response = self.session.put(
                self._content_url(remote_filename),
                headers={**self.headers, "Content-Type": "application/octet-stream"},
                data=source,
                timeout=300,
            )
        response.raise_for_status()
        return response.json()

    def _upload_large_file(
        self,
        local_file_path: Path,
        remote_filename: str,
        *,
        chunk_size: int,
    ) -> dict[str, Any]:
        response = self.session.post(
            self._upload_session_url(remote_filename),
            headers={**self.headers, "Content-Type": "application/json"},
            json={"item": {"@microsoft.graph.conflictBehavior": "replace", "name": remote_filename}},
            timeout=30,
        )
        response.raise_for_status()
        upload_url = str(response.json()["uploadUrl"])
        file_size = local_file_path.stat().st_size

        last_payload: dict[str, Any] = {}
        with local_file_path.open("rb") as source:
            start = 0
            while True:
                chunk = source.read(chunk_size)
                if not chunk:
                    break
                end = start + len(chunk) - 1
                upload_response = self.session.put(
                    upload_url,
                    headers={
                        "Content-Length": str(len(chunk)),
                        "Content-Range": f"bytes {start}-{end}/{file_size}",
                    },
                    data=chunk,
                    timeout=300,
                )
                upload_response.raise_for_status()
                if upload_response.content:
                    last_payload = upload_response.json()
                start = end + 1
        return last_payload

    def list_folder_children(self) -> list[dict[str, object]]:
        folder = _quote_drive_path(self.folder_path)
        next_url: str | None = (
            f"https://graph.microsoft.com/v1.0/sites/{self.site_id}/drive/root:/{folder}:/children"
        )
        items: list[dict[str, object]] = []
        while next_url:
            response = self.session.get(next_url, headers=self.headers, timeout=30)
            response.raise_for_status()
            payload = response.json()
            items.extend(payload.get("value", []))
            next_url = payload.get("@odata.nextLink")
        return items

    def delete_item(self, item_id: str) -> bool:
        response = self.session.post(
            f"https://graph.microsoft.com/v1.0/drives/{self.drive_id()}/items/{item_id}/permanentDelete",
            headers=self.headers,
            timeout=30,
        )
        if response.status_code in {204, 404}:
            return True
        response.raise_for_status()
        return True

    def download_and_hash(self, remote_content_url: str) -> str:
        response = self.session.get(remote_content_url, headers=self.headers, stream=True, timeout=300)
        response.raise_for_status()
        digest = hashlib.sha256()
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                digest.update(chunk)
        return digest.hexdigest()


def build_sharepoint_client(*, sharepoint_site: str, sharepoint_folder: str) -> SharePointClient:
    token = get_app_token()
    site_id = resolve_sharepoint_site_id(token, sharepoint_site)
    return SharePointClient(token=token, site_id=site_id, folder_path=sharepoint_folder)
