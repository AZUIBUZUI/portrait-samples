# -*- coding: utf-8 -*-
"""采集器注册表。新增采集来源时在这里注册即可。"""
from core.collectors.base import BaseCollector, CollectItem
from core.collectors.local_folder import LocalFolderCollector
from core.collectors.web import WebCollector, fetch_page_meta

REGISTRY = {
    LocalFolderCollector.source_type: LocalFolderCollector,
    WebCollector.source_type: WebCollector,
}


def get_collector(source_type: str, on_progress=None) -> BaseCollector:
    cls = REGISTRY.get(source_type)
    if cls is None:
        raise KeyError(f"未注册的采集来源: {source_type}")
    return cls(on_progress=on_progress)


def list_collectors() -> list:
    """给前端列出可用的采集来源。"""
    return [{"source_type": c.source_type, "display_name": c.display_name} for c in REGISTRY.values()]


__all__ = ["BaseCollector", "CollectItem", "get_collector", "list_collectors",
           "fetch_page_meta", "REGISTRY"]
