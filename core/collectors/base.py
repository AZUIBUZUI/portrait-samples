# -*- coding: utf-8 -*-
"""采集器抽象基类。

新增一种采集来源 = 在 collectors/ 下加一个继承 BaseCollector 的类，
再到 collectors/__init__.py 的 REGISTRY 里注册即可，不用改路由和前端。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator


# 表情包 / 梗图线索：文件名或图片地址里带这些词就不进样片库。
# 不看画面（没接视觉模型），只认文件名与动图格式 —— 人像样片不会是 gif。
MEME_KEYWORDS = (
    "表情包", "表情", "梗图", "斗图", "emoji", "meme", "sticker", "doutu", "biaoqing",
)


def looks_like_meme(name: str) -> bool:
    """按文件名 / 图片地址判断是不是表情包、梗图或动图。"""
    lowered = (name or "").strip().lower()
    if not lowered:
        return False
    if lowered.split("?")[0].endswith(".gif"):
        return True
    return any(key in lowered for key in MEME_KEYWORDS)


def is_gif_bytes(data: bytes) -> bool:
    """有些地址不带 .gif，只能看文件头判断动图。"""
    return bool(data) and data[:6] in (b"GIF87a", b"GIF89a")


@dataclass
class CollectItem:
    """采集到的单条素材，三种形态之一：本地路径 / 已下载字节 / 采集失败。"""
    path: str = ""                    # 本地已有图片的绝对路径
    data: bytes | None = None         # 下载或上传得到的图片字节
    filename: str = ""
    title: str = ""
    author: str = ""                  # 博主 / 摄影师
    source_url: str = ""              # 原网页地址
    shot_date: str = ""               # 拍摄日期 YYYY-MM-DD
    text: str = ""                    # 原帖正文文案
    hashtags: str = ""                # 作者自己打的话题标签，自动分类优先用它
    tags: list = field(default_factory=list)
    error: str = ""                   # 非空表示这条失败
    skip_reason: str = ""             # 非空表示这条被过滤掉（判定为 UI 元素），不算失败


class BaseCollector(ABC):
    """采集器基类：负责产出 CollectItem，落盘与入库交给 core.library。"""

    source_type = "base"
    display_name = "基础采集器"

    def __init__(self, on_progress=None):
        # 进度回调：on_progress(total=..., current=..., message=...)
        self.on_progress = on_progress or (lambda **kw: None)

    @abstractmethod
    def collect(self, target: str, **options) -> Iterator[CollectItem]:
        """target 为文件夹路径或 URL，逐条产出素材。"""

    def run(self, target: str, **options) -> Iterator[CollectItem]:
        """统一入口：包一层异常，单条失败不影响整体。"""
        try:
            yield from self.collect(target, **options)
        except Exception as exc:  # 采集源整体失败
            yield CollectItem(error=f"{self.display_name} 采集中断: {exc}")
