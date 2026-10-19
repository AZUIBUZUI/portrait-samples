# -*- coding: utf-8 -*-
"""本地文件夹批量导入采集器。"""
from pathlib import Path
from typing import Iterator

from core.collectors.base import BaseCollector, CollectItem, looks_like_meme
from core.storage import is_image


class LocalFolderCollector(BaseCollector):
    """递归扫描一个文件夹，把里面的图片全部收进来。

    options:
        recursive  bool  是否递归子目录，默认 True
        limit      int   最多取多少张，0 表示不限
    """

    source_type = "local"
    display_name = "本地文件夹"

    def collect(self, target: str, recursive: bool = True, limit: int = 0, **_) -> Iterator[CollectItem]:
        root = Path(target).expanduser()
        if not root.exists():
            yield CollectItem(error=f"文件夹不存在: {root}")
            return
        if not root.is_dir():
            yield CollectItem(error=f"不是文件夹: {root}")
            return

        pattern = "**/*" if recursive else "*"
        files = [p for p in sorted(root.glob(pattern)) if is_image(p)]
        if limit:
            files = files[:limit]

        total = len(files)
        self.on_progress(total=total, current=0, message=f"在 {root.name} 下找到 {total} 张图片")

        for index, path in enumerate(files, start=1):
            self.on_progress(total=total, current=index, message=path.name)
            if looks_like_meme(path.name):
                yield CollectItem(
                    path=str(path), filename=path.name,
                    skip_reason=f"{path.name} 像表情包 / 动图，已跳过",
                )
                continue
            yield CollectItem(
                path=str(path),
                filename=path.name,
                title=path.stem,
                # 用所在文件夹名做默认作者/主题线索，便于后续规则分类
                author=path.parent.name,
            )
