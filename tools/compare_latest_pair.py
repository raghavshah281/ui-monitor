# tools/compare_latest_pair.py
import os, glob, cv2, json, shutil
from pathlib import Path
from datetime import datetime
import numpy as np
import pandas as pd
from PIL import Image
from skimage.metrics import structural_similarity as ssim
import imagehash

SSIM_THRESHOLD  = float(os.environ.get("SSIM_THRESHOLD", "0.985"))
PHASH_THRESHOLD = int(os.environ.get("PHASH_THRESHOLD", "8"))

INPUT_LOCAL_ROOT  = os.environ.get("INPUT_LOCAL_ROOT", "data/screenshots")
REPORT_LOCAL_ROOT = os.environ.get("REPORT_LOCAL_ROOT", "out/diffs")
RUN_STAMP = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
OUTDIR = Path(REPORT_LOCAL_ROOT) / f"run_{RUN_STAMP}"

TS_FORMATS = ("%Y-%m-%d_%H-%M-%S","%Y%m%d-%H%M%S","%Y%m%d_%H%M%S","%Y-%m-%d %H.%M.%S")

def ensure_dir(p: Path): p.mkdir(parents=True, exist_ok=True)
def to_gray(img): return img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
def resize_to_min(a, b):
    h = min(a.shape[0], b.shape[0]); w = min(a.shape[1], b.shape[1])
    return cv2.resize(a, (w, h)), cv2.resize(b, (w, h))

def natural_key(p: str):
    stem = Path(p).stem
    from datetime import datetime
    for fmt in TS_FORMATS:
        try: return datetime.strptime(stem, fmt)
        except: pass
    try: return Path(p).stat().st_mtime
    except: return stem

def ssim_diff(a, b):
    a, b = resize_to_min(a, b)
    a_g, b_g = to_gray(a), to_gray(b)
    score, diff = ssim(a_g, b_g, full=True)
    diff = (1 - diff)
    dmin, dmax = float(diff.min()), float(diff.max())
    norm = ((diff - dmin) / (dmax - dmin + 1e-9) * 255).astype("uint8")
    heat = cv2.applyColorMap(norm, cv2.COLORMAP_JET)
    return float(score), heat, norm

def phash_distance(a_path, b_path):
    ha = imagehash.phash(Image.open(a_path))
    hb = imagehash.phash(Image.open(b_path))
    return ha - hb

def guess_zone(norm):
    h = norm.shape[0]
    band = h // 3 if h >= 3 else 1
    zones = [
        ("top",    norm[0:band, :].mean() if band>0 else 0),
        ("middle", norm[band:2*band, :].mean() if band>0 else 0),
        ("bottom", norm[2*band:, :].mean() if band>0 else 0),
    ]
    zones.sort(key=lambda x: x[1], reverse=True)
    return zones[0][0]

def main():
    pages_root = Path(INPUT_LOCAL_ROOT)
    ensure_dir(Path(REPORT_LOCAL_ROOT))
    ensure_dir(OUTDIR)

    folders = [p for p in sorted(pages_root.glob("*")) if p.is_dir()]
    rows = []

    for folder in folders:
        page = folder.name
        imgs = []
        for ext in ("*.png","*.jpg","*.jpeg","*.webp"):
            imgs += glob.glob(str(folder / ext))
        imgs = sorted(imgs, key=natural_key)
        if len(imgs) < 2:
            continue

        a_path, b_path = imgs[-2], imgs[-1]
        img_a, img_b = cv2.imread(a_path), cv2.imread(b_path)
        if img_a is None or img_b is None:
            continue

        ssim_score, heat, norm = ssim_diff(img_a, img_b)
        pdelta = int(phash_distance(a_path, b_path))
        changed = (ssim_score < SSIM_THRESHOLD) or (pdelta > PHASH_THRESHOLD)
        zone = guess_zone(norm) if changed else ""

        # Per-page out dir
        page_out = OUTDIR / page
        ensure_dir(page_out)

        # Heatmap
        heat_path = page_out / f"diff_{Path(a_path).name}_vs_{Path(b_path).name}.png"
        cv2.imwrite(str(heat_path), heat)

        # Copy the two source screenshots into the run folder (for reviewers)
        shutil.copy2(a_path, page_out / Path(a_path).name)
        shutil.copy2(b_path, page_out / Path(b_path).name)

        rows.append({
            "page": page,
            "prev_image": Path(a_path).name,
            "curr_image": Path(b_path).name,
            "ssim": round(ssim_score, 5),
            "phash_delta": pdelta,
            "changed": bool(changed),
            "likely_zone": zone,
            "heatmap": str(heat_path)
        })

    if not rows:
        print("No pages with at least two images.")
        return

    df = pd.DataFrame(rows).sort_values(["changed","page"], ascending=[False, True])
    csv_path = OUTDIR / "summary.csv"
    df.to_csv(csv_path, index=False)

    # Simple HTML
    html = [
        "<html><head><meta charset='utf-8'><title>UI Diff Report</title>",
        "<style>body{font-family:system-ui,Arial} table{border-collapse:collapse} td,th{border:1px solid #ddd;padding:6px} .chg{background:#ffecec}</style>",
        "</head><body>",
        f"<h2>UI Diff Report — {RUN_STAMP}</h2>",
        "<table><tr><th>Page</th><th>Prev</th><th>Curr</th><th>SSIM</th><th>pHash Δ</th><th>Flag</th><th>Zone</th><th>Heatmap</th></tr>"
    ]
    for r in df.to_dict("records"):
        cls = " class='chg'" if r["changed"] else ""
        html.append(
            f"<tr{cls}><td>{r['page']}</td><td>{r['prev_image']}</td><td>{r['curr_image']}</td>"
            f"<td>{r['ssim']}</td><td>{r['phash_delta']}</td>"
            f"<td>{'CHANGE' if r['changed'] else ''}</td><td>{r['likely_zone']}</td>"
            f"<td><img width='320' src='{Path(r['heatmap']).as_posix()}'/></td></tr>"
        )
    html += ["</table></body></html>"]
    html_path = OUTDIR / "report.html"
    with open(html_path, "w", encoding="utf-8") as f: f.write("\n".join(html))

    print("WROTE:", OUTDIR.as_posix())

if __name__ == "__main__":
    main()
