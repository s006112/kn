"""Nextcloud WebDAV upload and OCS public share helpers.

Used by:
* core_per_report.py
* helper/utils_odoo.py

Pipelines:
- env credentials -> ensure dirs -> upload file -> find share -> create share -> return urls

Invariants:
- Requests target `_BASE_URL`.
- Remote paths are URL-encoded by segment.
- Public share links are only for share type "3".

Out of scope:
- Token refresh, retry or backoff, pagination.
- Deleting remote files or shares.
- Non-public share types, custom permissions.
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Dict, Iterable, Tuple
from urllib.parse import quote

import requests
from dotenv import load_dotenv

_BASE_URL = "https://nextcloud.ampco.com.hk"
PEF_REMOTE_DIR = "/Documents/PER/Photometry Report"
PO_REMOTE_DIR = "/Documents/SO_Backup"
_OCS_HEADERS = {
    "OCS-APIRequest": "true",
    "Accept": "application/json",
}

log = logging.getLogger("nextcloud_upload")


def load_env() -> Tuple[str, str]:
    """Purpose:
    Load Nextcloud credentials from the environment (optionally via `.env`).

    Inputs:
    None.

    Outputs:
    `(username, password)`.

    Side effects:
    Loads `.env` and reads `NEXTCLOUD_USERNAME` / `NEXTCLOUD_PASSWORD`.

    Failure modes:
    `RuntimeError` when required variables are missing.
    """
    load_dotenv()  # Ensures .env is read when running locally
    username = os.getenv("NEXTCLOUD_USERNAME")
    password = os.getenv("NEXTCLOUD_PASSWORD")
    missing = []
    if not username:
        missing.append("NEXTCLOUD_USERNAME")
    if not password:
        missing.append("NEXTCLOUD_PASSWORD")
    if missing:
        raise RuntimeError(f"Missing required Nextcloud env vars: {', '.join(missing)}")
    assert username is not None and password is not None
    return username, password


def mkcol_recursive(base_url: str, username: str, auth: Tuple[str, str], folders: Iterable[str]) -> None:
    """Purpose:
    Ensure each folder exists via ordered WebDAV `MKCOL` requests.

    Inputs:
    `base_url`, `username`, `auth`, `folders`.

    Outputs:
    None.

    Side effects:
    WebDAV requests.

    Failure modes:
    `RuntimeError` for 401/409/other 4xx-5xx responses.
    """
    encoded_username = quote(username, safe="")
    base_url = base_url.rstrip("/")
    root = f"{base_url}/remote.php/dav/files/{encoded_username}"

    session = requests.Session()
    session.auth = auth

    current = root
    for folder in folders:
        if not folder:
            continue
        current = f"{current}/{quote(folder, safe='')}"
        resp = session.request("MKCOL", current, timeout=30)
        if resp.status_code in (200, 201):
            continue
        if resp.status_code == 405:  # MKCOL returns 405 when the collection already exists.
            continue
        if resp.status_code == 409:
            raise RuntimeError(f"Cannot create folder '{folder}': parent does not exist (409)")
        if resp.status_code == 401:
            raise RuntimeError("Nextcloud authentication failed while creating folders (401)")
        if resp.status_code >= 400:
            detail = (resp.text or "").strip()
            snippet = f": {detail[:200]}" if detail else ""
            raise RuntimeError(f"Failed to create folder '{folder}' ({resp.status_code}){snippet}")


def upload_file(
    local_path: str,
    remote_dir: str,
    base_url: str,
    username: str,
    auth: Tuple[str, str],
) -> str:
    """Purpose:
    Upload a local file via WebDAV and return the remote path.

    Inputs:
    `local_path`, `remote_dir`, `base_url`, `username`, `auth`.

    Outputs:
    Remote path string with a leading slash and unencoded segments.

    Side effects:
    Reads the local file and performs a WebDAV `PUT`.

    Failure modes:
    `FileNotFoundError` if local file is missing; `RuntimeError` on non-200/201/204 upload response.
    """
    local_file = Path(local_path).expanduser()
    if not local_file.is_file():
        raise FileNotFoundError(f"Local file not found: {local_file}")

    base_url = base_url.rstrip("/")
    encoded_username = quote(username, safe="")
    remote_root = f"{base_url}/remote.php/dav/files/{encoded_username}"

    remote_segments = [segment for segment in remote_dir.strip("/").split("/") if segment]
    encoded_dir = "/".join(quote(seg, safe="") for seg in remote_segments if seg)
    if encoded_dir:
        remote_root = f"{remote_root}/{encoded_dir}"

    remote_url = f"{remote_root}/{quote(local_file.name, safe='')}"

    with local_file.open("rb") as handle:
        resp = requests.put(remote_url, data=handle, auth=auth, timeout=60)
    if resp.status_code not in (200, 201, 204):
        detail = (resp.text or "").strip()
        snippet = f": {detail[:200]}" if detail else ""
        raise RuntimeError(f"Failed to upload '{local_file.name}' ({resp.status_code}){snippet}")

    remote_path = "/" + "/".join(remote_segments + [local_file.name])
    return remote_path


def _format_share_payload(share: Dict[str, object]) -> Dict[str, str]:
    """Purpose:
    Normalize a Nextcloud share payload into `page`/`download`/`id`.

    Inputs:
    `share` dict from Nextcloud.

    Outputs:
    Dict with `page`, `download`, `id` keys.

    Side effects:
    None.

    Failure modes:
    `RuntimeError` if `url` or `id` is missing.
    """
    url = share.get("url")
    share_id = share.get("id")
    if not url or not share_id:
        raise RuntimeError("Unexpected share payload from Nextcloud (missing url or id)")
    url_str = str(url)
    return {
        "page": url_str,
        "download": f"{url_str}/download",
        "id": str(share_id),
    }


def get_public_share_if_exists(base_url: str, auth: Tuple[str, str], remote_path: str) -> Dict[str, str] | None:
    """Purpose:
    Fetch existing public share data for `remote_path`, if present.

    Inputs:
    `base_url`, `auth`, `remote_path`.

    Outputs:
    Share dict for share type "3", or `None`.

    Side effects:
    OCS share listing request.

    Failure modes:
    `RuntimeError` on HTTP errors or invalid JSON.
    """
    base_url = base_url.rstrip("/")
    path_param = f"/{remote_path.lstrip('/')}"
    params = {"path": path_param, "format": "json"}
    url = f"{base_url}/ocs/v2.php/apps/files_sharing/api/v1/shares"
    resp = requests.get(url, headers=_OCS_HEADERS, params=params, auth=auth, timeout=30)
    if resp.status_code == 404:
        return None
    if resp.status_code >= 400:
        detail = (resp.text or "").strip()
        snippet = f": {detail[:200]}" if detail else ""
        raise RuntimeError(f"Failed to query share info ({resp.status_code}){snippet}")

    try:
        payload = resp.json()
    except ValueError as exc:  # Defensive: OCS responses may be non-JSON on error.
        raise RuntimeError("Nextcloud returned invalid JSON for share listing") from exc

    data = payload.get("ocs", {}).get("data")
    if not data:
        return None
    shares = data if isinstance(data, list) else [data]
    for share in shares:
        share_type = share.get("share_type")
        if str(share_type) == "3":
            return _format_share_payload(share)
    return None


def create_or_get_public_share(base_url: str, auth: Tuple[str, str], remote_path: str) -> Dict[str, str]:
    """Purpose:
    Reuse an existing public share for `remote_path` or create a new one.

    Inputs:
    `base_url`, `auth`, `remote_path`.

    Outputs:
    Share dict with `page`, `download`, `id`.

    Side effects:
    OCS GET and possibly OCS POST requests.

    Failure modes:
    `RuntimeError` on HTTP errors, invalid JSON, or missing share data.
    """
    existing = get_public_share_if_exists(base_url, auth, remote_path)
    if existing:
        return existing

    base_url = base_url.rstrip("/")
    path_param = f"/{remote_path.lstrip('/')}"
    url = f"{base_url}/ocs/v2.php/apps/files_sharing/api/v1/shares"
    data = {
        "shareType": "3",
        "path": path_param,
        "permissions": "1",
    }

    resp = requests.post(url, headers=_OCS_HEADERS, data=data, auth=auth, timeout=30)
    if resp.status_code >= 400:
        detail = (resp.text or "").strip()
        snippet = f": {detail[:200]}" if detail else ""
        raise RuntimeError(f"Failed to create public share ({resp.status_code}){snippet}")

    try:
        payload = resp.json()
    except ValueError as exc:  # Defensive: OCS responses may be non-JSON on error.
        raise RuntimeError("Nextcloud returned invalid JSON for share creation") from exc

    data = payload.get("ocs", {}).get("data")
    if not data:
        raise RuntimeError("Nextcloud did not return share details after creation")
    share = data if isinstance(data, dict) else data[0]
    return _format_share_payload(share)


def upload_and_share_file(local_path: str, remote_dir: str) -> Dict[str, str]:
    """Purpose:
    Upload a file and return public share URLs (when available) for the uploaded remote path.

    Inputs:
    `local_path`, `remote_dir`.

    Outputs:
    Dict with `remote_path` and (when available) `page`/`download`/`id`.

    Side effects:
    Loads credentials, creates directories, uploads file, and attempts OCS share creation.

    Failure modes:
    Propagates credential/directory/upload errors; logs and swallows share creation errors.
    """
    username, password = load_env()
    auth = (username, password)

    folders = [segment for segment in remote_dir.strip("/").split("/") if segment]
    if folders:
        mkcol_recursive(_BASE_URL, username, auth, folders)

    remote_path = upload_file(local_path, remote_dir, _BASE_URL, username, auth)

    try:
        share_payload = create_or_get_public_share(_BASE_URL, auth, remote_path)
    except Exception as exc:  # Share link failures should not block a successful upload.
        log.warning("Unable to create share link for %s: %s", remote_path, exc)
        return {"remote_path": remote_path}

    return {
        "remote_path": remote_path,
        **share_payload,
    }


def ushare(local_path: str, remote_dir: str) -> Dict[str, str]:
    """Purpose:
    Convenience wrapper around `upload_and_share_file` for an arbitrary remote directory.

    Inputs:
    `local_path`, `remote_dir`.

    Outputs:
    Same as `upload_and_share_file`.

    Side effects:
    Same as `upload_and_share_file`.

    Failure modes:
    Same as `upload_and_share_file`.
    """
    return upload_and_share_file(local_path, remote_dir)


__all__ = [
    "PEF_REMOTE_DIR",
    "PO_REMOTE_DIR",
    "mkcol_recursive",
    "upload_file",
    "get_public_share_if_exists",
    "create_or_get_public_share",
    "upload_and_share_file",
    "ushare",
]
