# -*- coding: utf-8 -*-
"""按画面特征分类 / 提取特征。

两种用法：
1. `classify()`：用亮度 / 饱和度 / 对比度 / 冷暖做一个快速猜测，只用于给人工纠正挑初始样本；
2. `feature_vector()` + `knn_predict()`：提取颜色直方图、明暗分布、边缘密度等特征，
   以人工纠正过的样片为训练集做最近邻分类（"纠正 → 训练 → 自动分"的正式通路）。
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageStat

CATEGORY_KEYS = ("quiet", "energetic", "emotional", "cool")

# CLIP 视觉编码器（量化版，约 85MB）。没有模型文件时自动退回手工特征。
MODEL_DIR = Path(__file__).resolve().parents[1] / "data" / "models"
CLIP_MODEL = MODEL_DIR / "clip_vision_quantized.onnx"
CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)

_clip_session = None
_clip_tried = False


def _clip() -> object | None:
    """懒加载 CLIP 视觉模型；没装 onnxruntime 或没模型文件时返回 None。"""
    global _clip_session, _clip_tried
    if _clip_tried:
        return _clip_session
    _clip_tried = True
    try:
        import onnxruntime as ort
        if CLIP_MODEL.exists():
            _clip_session = ort.InferenceSession(
                str(CLIP_MODEL), providers=["CPUExecutionProvider"])
    except Exception:
        _clip_session = None
    return _clip_session


def image_features(path: str | Path) -> dict:
    """取缩略图的画面特征；读不出来返回空字典。"""
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((200, 200))
            gray = image.convert("L")
            hsv = image.convert("HSV")
            gray_stat = ImageStat.Stat(gray)
            red, _, blue = [ImageStat.Stat(channel).mean[0] for channel in image.split()]
            return {
                "brightness": gray_stat.mean[0],   # 0-255，越大越亮
                "contrast": gray_stat.stddev[0],   # 0-255，越大越硬
                "saturation": ImageStat.Stat(hsv).mean[1],  # 0-255
                "warmth": red - blue,              # >0 偏暖，<0 偏冷
            }
    except Exception:
        return {}


def classify(path: str | Path) -> str:
    """返回 quiet / energetic / emotional / cool 之一，读不出图返回空串。"""
    features = image_features(path)
    if not features:
        return ""
    return score(features)


def score(features: dict) -> str:
    """把画面特征折算成四类得分，取最高。"""
    bright = min(max(features["brightness"] / 255, 0), 1)
    saturation = min(max(features["saturation"] / 255, 0), 1)
    contrast = min(max(features["contrast"] / 80, 0), 1)
    warm = min(max((features["warmth"] + 40) / 80, 0), 1)

    scores = {
        # 明亮 + 高饱和 + 偏暖
        "energetic": 0.38 * bright + 0.34 * saturation + 0.28 * warm,
        # 明亮 + 低饱和 + 柔和
        "quiet": 0.50 * bright + 0.32 * (1 - saturation) + 0.18 * (1 - contrast),
        # 偏暗 + 低饱和 + 偏冷 + 有层次
        "emotional": 0.38 * (1 - bright) + 0.12 * (1 - saturation)
                     + 0.24 * contrast + 0.26 * (1 - warm),
        # 偏暗 + 高对比 + 高饱和
        "cool": 0.20 * (1 - bright) + 0.42 * contrast + 0.38 * saturation,
    }
    return max(scores, key=scores.get)


# ============================================================
# 特征向量 + 最近邻（人工纠正过的样本作为训练集）
# ============================================================
def feature_vector(path: str | Path) -> np.ndarray | None:
    """图片特征向量：优先 CLIP 视觉编码器（512 维，单位向量），没有模型时退回手工特征。"""
    session = _clip()
    if session is not None:
        vector = _clip_vector(path, session)
        if vector is not None:
            return vector
    return _handcrafted_vector(path)


def _clip_vector(path: str | Path, session) -> np.ndarray | None:
    """CLIP 图像编码：短边缩放到 224 后中心裁切，归一化后推理。"""
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            width, height = image.size
            scale = 224 / min(width, height)
            image = image.resize(
                (max(224, round(width * scale)), max(224, round(height * scale))),
                Image.BICUBIC,
            )
            left = (image.width - 224) // 2
            top = (image.height - 224) // 2
            image = image.crop((left, top, left + 224, top + 224))
            arr = np.asarray(image, dtype=np.float32) / 255.0
        arr = (arr - CLIP_MEAN) / CLIP_STD
        tensor = np.transpose(arr, (2, 0, 1))[None]
        out = session.run(None, {"pixel_values": tensor})[0][0]
        vector = np.asarray(out, dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm else None
    except Exception:
        return None


def _handcrafted_vector(path: str | Path) -> np.ndarray | None:
    """没有 CLIP 时的手工特征：颜色直方图 + 明暗分布 + 全局统计 + 边缘密度。"""
    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((96, 96))
            arr = np.asarray(image, dtype=np.float32) / 255.0
            hsv = np.asarray(image.convert("HSV"), dtype=np.float32) / 255.0
            gray = arr @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
            total = float(gray.size) or 1.0

            h_hist, _ = np.histogram(hsv[..., 0], bins=12, range=(0, 1))
            s_hist, _ = np.histogram(hsv[..., 1], bins=4, range=(0, 1))
            v_hist, _ = np.histogram(hsv[..., 2], bins=4, range=(0, 1))

            height, width = gray.shape
            grid = []
            for row in range(3):
                for col in range(3):
                    patch = gray[int(height * row / 3):int(height * (row + 1) / 3),
                                 int(width * col / 3):int(width * (col + 1) / 3)]
                    grid.append(float(patch.mean()) if patch.size else 0.0)

            gy, gx = np.gradient(gray)
            edge = float(np.sqrt(gx * gx + gy * gy).mean())

            return np.concatenate([
                h_hist / total, s_hist / total, v_hist / total,
                np.asarray(grid, dtype=np.float32),
                np.asarray([
                    float(gray.mean()), float(gray.std()),
                    float(hsv[..., 1].mean()),
                    float(arr[..., 0].mean() - arr[..., 2].mean()),
                    edge,
                ], dtype=np.float32),
            ]).astype(np.float32)
    except Exception:
        return None


def prepare(pairs: list) -> tuple:
    """pairs: [(特征向量, 分类id)] → (L2 归一化的特征矩阵, 标签数组)。"""
    matrix = np.stack([pair[0] for pair in pairs]).astype(np.float32)
    labels = np.asarray([pair[1] for pair in pairs])
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms < 1e-9] = 1.0
    return matrix / norms, labels


def knn_predict(vector: np.ndarray, train_x: np.ndarray, labels: np.ndarray,
                k: int = 3) -> int:
    """余弦相似度 + 距离加权投票，返回票数最高的分类 id。"""
    x = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(x))
    if norm:
        x = x / norm
    similarity = train_x @ x
    distance = 1.0 - similarity
    k = max(1, min(k, len(labels)))
    nearest = np.argsort(distance)[:k]
    weights = 1.0 / (distance[nearest] + 1e-6)
    scores: dict = {}
    for index, weight in zip(nearest, weights):
        label = int(labels[index])
        scores[label] = scores.get(label, 0.0) + float(weight)
    return max(scores, key=scores.get)


def loo_accuracy(pairs: list, k: int = 3) -> float | None:
    """留一法准确率：每次留出一张、用其余样本预测它。样本太少时返回 None。"""
    if len(pairs) < 4:
        return None
    train_x, labels = prepare(pairs)
    correct = 0
    for index in range(len(labels)):
        keep = np.ones(len(labels), dtype=bool)
        keep[index] = False
        predicted = knn_predict(pairs[index][0], train_x[keep], labels[keep], k)
        correct += int(predicted == int(labels[index]))
    return round(correct / len(labels), 3)
