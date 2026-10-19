# -*- coding: utf-8 -*-
"""存储层：文件落盘、哈希去重、缩略图生成、图片元信息读取。

只处理"文件"，不碰数据库；数据库写入由 core.library 负责。
"""
import hashlib
import io
import shutil
from datetime import datetime
from pathlib import Path

from config import IMAGE_EXTS, ORIGINALS_DIR, THUMB_MAX, THUMBS_DIR, TMP_DIR

try:
    from PIL import Image
    THUMB_ENABLED = True
except ImportError:  # 未安装 Pillow 时降级为"不生成缩略图"
    Image = None
    THUMB_ENABLED = False


# ---------- 基础 ----------
def ensure_dirs() -> None:
    for d in (ORIGINALS_DIR, THUMBS_DIR, TMP_DIR):
        d.mkdir(parents=True, exist_ok=True)


def is_image(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_EXTS


def file_hash(path: Path, chunk: int = 1 << 20) -> str:
    """内容哈希，同图不同文件名也能识别出来。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            data = f.read(chunk)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


def image_size(data: bytes) -> tuple[int, int]:
    """从图片字节流读真实像素尺寸；读不出来返回 (0, 0)（调用方据此放弃判断）。"""
    if not THUMB_ENABLED:
        return (0, 0)
    try:
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:
        return (0, 0)


def pick_dir(shot_date: str = "") -> Path:
    """原图分目录存放：年/月 优先取拍摄日期，取不到用当前年月。"""
    stamp = shot_date.strip()
    if len(stamp) >= 7:
        year, month = stamp[:4], stamp[5:7]
    else:
        now = datetime.now()
        year, month = f"{now.year}", f"{now.month:02d}"
    return ORIGINALS_DIR / year / month


# ---------- 落盘 ----------
def store_file(src: Path, shot_date: str = "") -> tuple[str, str]:
    """把一张图片存入库目录，返回 (相对路径, 绝对路径)。同名自动加序号。"""
    target_dir = pick_dir(shot_date)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / src.name
    if target.exists() and file_hash(target) != file_hash(src):
        target = target_dir / f"{src.stem}_{datetime.now():%H%M%S%f}{src.suffix}"
    shutil.copy2(src, target)
    return str(target.relative_to(ORIGINALS_DIR)).replace("\\", "/"), str(target)


def store_bytes(data: bytes, filename: str, shot_date: str = "") -> tuple[str, str]:
    """把内存中的图片字节落盘（上传 / 下载用）。"""
    target_dir = pick_dir(shot_date)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    if target.exists():
        target = target_dir / f"{Path(filename).stem}_{datetime.now():%H%M%S%f}{Path(filename).suffix}"
    target.write_bytes(data)
    return str(target.relative_to(ORIGINALS_DIR)).replace("\\", "/"), str(target)


def remove_file(rel_path: str) -> None:
    """删除库内文件，路径以原图目录为基准。"""
    if not rel_path:
        return
    (ORIGINALS_DIR / rel_path).unlink(missing_ok=True)


# ---------- 缩略图 ----------
def make_thumb(abs_path: str, rel_path: str) -> tuple[str, int, int, str]:
    """生成缩略图，返回 (缩略图相对路径, 宽, 高, 拍摄日期)。

    读取失败时返回空缩略图路径，不影响主流程。
    """
    if not THUMB_ENABLED:
        return "", 0, 0, ""

    thumb_rel, width, height, shot_date = "", 0, 0, ""
    with Image.open(abs_path) as im:
        width, height = im.size
        shot_date = _read_shot_date(im)
        im = im.convert("RGB")
        im.thumbnail(THUMB_MAX)
        thumb_abs = THUMBS_DIR / f"{Path(rel_path).stem}.jpg"
        im.save(thumb_abs, "JPEG", quality=82)
        thumb_rel = str(thumb_abs.relative_to(THUMBS_DIR)).replace("\\", "/")
    return thumb_rel, width, height, shot_date


def _read_shot_date(im) -> str:
    """从 EXIF 取拍摄日期，取不到返回空。"""
    try:
        exif = im.getexif()
        raw = exif.get(36867) or exif.get(306) or ""   # DateTimeOriginal / DateTime
        if raw and len(raw) >= 10:
            return raw[:10].replace(":", "-")
    except Exception:
        pass
    return ""


def guess_filename(url_or_name: str, fallback: str = "sample.jpg") -> str:
    """从 URL / 文件名里推断一个安全的文件名。"""
    name = Path(url_or_name.split("?")[0]).name or fallback
    if Path(name).suffix.lower() not in IMAGE_EXTS:
        name = f"{Path(name).stem or 'sample'}.jpg"
    return name
