import os, re, time
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

# ---------- Auth ----------
def drive_client_from_service_account_json(json_content: str) -> GoogleDrive:
    creds_path = "/tmp/sa.json"
    with open(creds_path, "w", encoding="utf-8") as f:
        f.write(json_content)
    gauth = GoogleAuth(settings={
        "client_config_backend": "service",
        "service_config": {"client_json_file_path": creds_path}
    })
    gauth.ServiceAuth()
    return GoogleDrive(gauth)

# ---------- Helpers ----------
def list_children(drive: GoogleDrive, parent_id: str) -> List[dict]:
    # List direct children (files & folders) under a folder ID
    q = f"'{parent_id}' in parents and trashed=false"
    return drive.ListFile({
        "q": q,
        "supportsAllDrives": True,
        "includeItemsFromAllDrives": True
    }).GetList()

def ensure_folder(drive: GoogleDrive, parent_id: str, name: str) -> dict:
    for item in list_children(drive, parent_id):
        if item["mimeType"] == "application/vnd.google-apps.folder" and item["title"] == name:
            return item
    f = drive.CreateFile({
        "title": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [{"id": parent_id}],
        "supportsAllDrives": True
    })
    f.Upload(param={'supportsAllDrives': True})
    return f

def create_folder(drive: GoogleDrive, parent_id: str, name: str) -> dict:
    f = drive.CreateFile({
        "title": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [{"id": parent_id}],
        "supportsAllDrives": True
    })
    f.Upload(param={'supportsAllDrives': True})
    return f

def set_anyone_with_link(drive_file):
    try:
        drive_file.InsertPermission({"type": "anyone", "role": "reader"})
    except Exception as e:
        print(f"[warn] link-sharing may be blocked by org policy: {e}")

def _parse_gdrive_datetime(s: str) -> float:
    """
    Google Drive v2 returns ISO 8601 like '2025-11-08T05:57:12.345Z'
    Return POSIX epoch seconds (UTC).
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.timestamp()
    except Exception:
        return None

def download_folder_tree(drive: GoogleDrive, root_id: str, local_root: str):
    """
    Mirror: root_id (Drive) -> local_root (disk)
    Expected structure under root:
      <page1>/images...
      <page2>/images...
    For each file we:
      - download only image types
      - set local mtime to Drive createdDate (or modifiedDate) to preserve chronology
    """
    Path(local_root).mkdir(parents=True, exist_ok=True)

    def recurse(folder_id: str, local_path: Path):
        items = list_children(drive, folder_id)
        for item in items:
            if item["mimeType"] == "application/vnd.google-apps.folder":
                sub_local = local_path / item["title"]
                sub_local.mkdir(parents=True, exist_ok=True)
                recurse(item["id"], sub_local)
            else:
                title = item["title"]
                if not re.search(r"\.(png|jpg|jpeg|webp)$", title, re.IGNORECASE):
                    continue

                dst = local_path / title
                # fetch full metadata to get created/modified dates
                fh = drive.CreateFile({"id": item["id"]})
                fh.FetchMetadata()  # ensures createdDate/modifiedDate present
                created = fh.metadata.get("createdDate") or ""
                modified = fh.metadata.get("modifiedDate") or ""

                fh.GetContentFile(dst.as_posix())
                # set local timestamps so sorting by mtime is meaningful
                ts = _parse_gdrive_datetime(created) or _parse_gdrive_datetime(modified)
                if ts:
                    os.utime(dst.as_posix(), (ts, ts))

    recurse(root_id, Path(local_root))

def upload_run_folder(drive: GoogleDrive, reports_parent_id: str, local_run_dir: str) -> str:
    """
    Upload the entire run_... folder back to Drive under UI Monitor Reports.
    Returns a sharable link to the run folder.
    """
    local_run = Path(local_run_dir)
    run_drive_folder = create_folder(drive, reports_parent_id, local_run.name)
    set_anyone_with_link(run_drive_folder)

    def recurse_upload(local_dir: Path, parent_drive_id: str):
        for p in sorted(local_dir.iterdir()):
            if p.is_dir():
                d = create_folder(drive, parent_drive_id, p.name)
                recurse_upload(p, d["id"])
            else:
                f = drive.CreateFile({
                    "title": p.name,
                    "parents": [{"id": parent_drive_id}],
                    "supportsAllDrives": True
                })
                f.SetContentFile(p.as_posix())
                f.Upload(param={'supportsAllDrives': True})
                print(f"[uploaded] {p.name} -> parent {parent_drive_id}")

    recurse_upload(local_run, run_drive_folder["id"])

    # Optional: quick listing for logs
    children = list_children(drive, run_drive_folder["id"])
    print(f"[listing] items in {local_run.name}:")
    for c in children:
        print(" -", c["title"], c["mimeType"])

    return f"https://drive.google.com/drive/folders/{run_drive_folder['id']}"
