# tools/drive_sync.py
import os, io, json, time, re
from pathlib import Path
from typing import List, Dict
from pydrive2.auth import GoogleAuth
from pydrive2.drive import GoogleDrive

# ---------- Auth ----------
def drive_client_from_service_account_json(json_content: str) -> GoogleDrive:
    # json_content is the full JSON string (from GitHub secret)
    creds_path = "/tmp/sa.json"
    with open(creds_path, "w", encoding="utf-8") as f:
        f.write(json_content)
    gauth = GoogleAuth(settings={
        "client_config_backend": "service",
        "service_config": {
            "client_json_file_path": creds_path
        }
    })
    gauth.ServiceAuth()
    return GoogleDrive(gauth)

# ---------- Helpers ----------
def list_children(drive: GoogleDrive, parent_id: str) -> List[dict]:
    # List direct children (files & folders) under a folder ID
    q = f"'{parent_id}' in parents and trashed=false"
    return drive.ListFile({"q": q, "supportsAllDrives": True, "includeItemsFromAllDrives": True}).GetList()

def ensure_folder(drive: GoogleDrive, parent_id: str, name: str) -> dict:
    # Create (or find) a subfolder by name under parent_id
    for item in list_children(drive, parent_id):
        if item["mimeType"] == "application/vnd.google-apps.folder" and item["title"] == name:
            return item
    f = drive.CreateFile({
        "title": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [{"id": parent_id}],
        "supportsAllDrives": True
    })
    f.Upload()
    return f

def create_folder(drive: GoogleDrive, parent_id: str, name: str) -> dict:
    f = drive.CreateFile({
        "title": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [{"id": parent_id}],
        "supportsAllDrives": True
    })
    f.Upload()
    return f

def set_anyone_with_link(drive_file):
    # Make folder/file accessible to anyone with the link (view)
    drive_file.InsertPermission({
        "type": "anyone",
        "role": "reader"
    })

def download_folder_tree(drive: GoogleDrive, root_id: str, local_root: str):
    """
    Mirror: root_id (Drive) -> local_root (disk)
    We expect the structure:
      Daily Screenshot Root/
        <page1>/
          images...
        <page2>/
          ...
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
                # download file if it looks like an image
                title = item["title"]
                if not re.search(r"\.(png|jpg|jpeg|webp)$", title, re.IGNORECASE):
                    continue
                dst = local_path / title
                if not dst.exists():
                    fh = drive.CreateFile({"id": item["id"]})
                    fh.GetContentFile(dst.as_posix())
    recurse(root_id, Path(local_root))

def upload_run_folder(drive: GoogleDrive, reports_parent_id: str, local_run_dir: str) -> str:
    """
    Upload the entire run_... folder back to Drive under UI Monitor Reports.
    Returns a sharable link (anyone with link).
    """
    local_run = Path(local_run_dir)
    run_drive_folder = create_folder(drive, reports_parent_id, local_run.name)
    set_anyone_with_link(run_drive_folder)  # share the folder

    def recurse_upload(local_dir: Path, parent_drive_id: str):
        for p in local_dir.iterdir():
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
                f.Upload()
    recurse_upload(local_run, run_drive_folder["id"])
    # Return a link to the folder
    return f"https://drive.google.com/drive/folders/{run_drive_folder['id']}"
