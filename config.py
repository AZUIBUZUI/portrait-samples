# -*- coding: utf-8 -*-
"""全局配置：路径、尺寸、服务参数。"""
from pathlib import Path

# ---------- 目录 ----------
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
ORIGINALS_DIR = DATA_DIR / "originals"   # 原图落盘位置（按 年/月 分目录）
THUMBS_DIR = DATA_DIR / "thumbs"         # 缩略图
TMP_DIR = DATA_DIR / "tmp"               # 下载/解压临时文件
DB_PATH = DATA_DIR / "library.db"

# ---------- 素材规则 ----------
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
THUMB_MAX = (600, 600)                   # 缩略图最长边（等比）
MAX_UPLOAD_MB = 50                       # 单张上传上限
# 入库分辨率下限：真实像素小边小于该值的图不进库（图标 / logo / 糊图）。
# 写真样片成片普遍短边 ≥1080，720 能挡住绝大多数缩略图与表情包；采集页可当场调高。
WEB_MIN_SIDE = 720

# ---------- 服务 ----------
HOST = "127.0.0.1"
PORT = 5000
DEBUG = True

# ---------- 预留：AI 自动分类 ----------
# 阶段一为规则匹配；接入模型后在此填写服务地址与密钥
AI_ENABLED = False
AI_ENDPOINT = ""
AI_API_KEY = ""
