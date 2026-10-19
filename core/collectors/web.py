# -*- coding: utf-8 -*-
"""网页 / 社交媒体采集器。

两条通路：
1. 图片直链 → 直接下载入库（不做过滤，因为是你明确指定的那张）。
2. 页面地址 → 抽取候选图 → 先按 URL 特征和页面声明尺寸剔除 UI 元素
   （logo / 图标 / 占位图）→ 逐张下载 → 下载后再用真实像素尺寸兜底判断一次，
   明显偏小的丢弃，只保留样片。

过滤掉的数量会记在采集任务里，不会静默丢掉。

小红书 / 微博 等站点需要登录态和 JS 渲染，专属解析在 PLATFORM_HANDLERS 里预留。
"""
import json
import re
from datetime import datetime
from typing import Iterator
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from config import WEB_MIN_SIDE
from core.collectors.base import BaseCollector, CollectItem, is_gif_bytes, looks_like_meme
from core.storage import guess_filename, image_size

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Referer": "https://www.google.com/",
}
TIMEOUT = 15
MAX_BYTES = 20 * 1024 * 1024          # 单张图片下载上限 20MB

# URL 里出现这些词，基本可以断定是 UI 元素而不是样片
UI_IMAGE_KEYS = (
    "logo", "avatar", "icon", "sprite", "qrcode", "blank", "loading",
    "watermark", "placeholder", "default_", "share", "emoji", "badge",
    "arrow", "btn_", "button", "banner", "pixel", "spacer",
)
# 平台前端自己的静态资源域名：这些域名上的图都是界面骨架（logo / 图标 / 占位 / 默认图）
UI_ASSET_HOSTS = (
    "fe-static.xhscdn.com",            # 小红书前端静态资源
    "picasso-static.xiaohongshu.com",  # 小红书平台图（og 默认图、头像等）
)

# 预留：需要登录态/专属解析的平台。键为域名关键字。
PLATFORM_HANDLERS = {
    "xiaohongshu.com": "小红书（预留：需登录 Cookie + 签名参数，待接入 playwright 方案）",
    "weibo.com": "微博（预留：需处理图床防盗链与登录态）",
    "weibo.cn": "微博（预留：需处理图床防盗链与登录态）",
    "douyin.com": "抖音（预留：需处理动态渲染）",
    "zhihu.com": "知乎（预留：需处理反爬）",
}


class WebCollector(BaseCollector):
    """target 支持多条 URL，换行或逗号分隔。

    options:
        max_images  int  单个页面最多取多少张候选图，默认 30
        min_side    int  真实像素小边小于该值的图判为图标丢弃，默认 config.WEB_MIN_SIDE
    """

    source_type = "web"
    display_name = "网页/社媒"

    def collect(self, target: str, max_images: int = 30,
                min_side: int = WEB_MIN_SIDE, **_) -> Iterator[CollectItem]:
        urls = [u.strip() for u in target.replace(",", "\n").splitlines() if u.strip()]
        if not urls:
            yield CollectItem(error="没有解析到有效地址")
            return

        max_images = _to_int(max_images, 30)
        min_side = _to_int(min_side, WEB_MIN_SIDE)

        for page_url in urls:
            if _looks_like_image(page_url):
                yield self._download_direct(page_url)
            else:
                yield from self._collect_from_page(page_url, max_images, min_side)

    # ---------- 单张直链 ----------
    def _download_direct(self, url: str) -> CollectItem:
        # 直链是你指名要的那一张，不做内容过滤（保持原有约定）
        try:
            data = _download(url)
        except Exception as exc:
            return CollectItem(source_url=url, error=f"下载失败: {exc}")
        self.on_progress(current=1, total=1, message=guess_filename(url))
        return CollectItem(data=data, filename=guess_filename(url), source_url=url)

    # ---------- 整页抽取 ----------
    def _collect_from_page(self, page_url: str, max_images: int, min_side: int):
        try:
            html = fetch_page(page_url)
        except Exception as exc:
            yield CollectItem(source_url=page_url, error=f"打开页面失败: {exc}{_platform_hint(page_url)}")
            return

        page = _page_meta(html)
        images = _extract_images(html, page_url)[:max_images]
        if not images:
            yield CollectItem(
                source_url=page_url,
                error=f"页面里没找到可用图片{_platform_hint(page_url)}",
            )
            return

        self.on_progress(total=len(images), current=0,
                         message=f"《{page['title'] or page_url}》筛出 {len(images)} 张候选")
        for index, img_url in enumerate(images, start=1):
            self.on_progress(total=len(images), current=index, message=guess_filename(img_url))
            try:
                data = download_image(img_url)
            except Exception as exc:
                yield CollectItem(source_url=img_url, title=page["title"], error=f"下载失败: {exc}")
                continue

            # 表情包 / 梗图 / 动图：文件名线索或 gif 文件头，直接判为非样片
            filename = guess_filename(img_url)
            if looks_like_meme(filename) or is_gif_bytes(data):
                yield CollectItem(
                    source_url=img_url, title=page["title"],
                    skip_reason="表情包 / 梗图 / 动图，不进样片库",
                )
                continue

            # 兜底：下载后按真实像素判断，UI 元素通常很小
            width, height = image_size(data)
            if width and height and min(width, height) < min_side:
                yield CollectItem(
                    source_url=img_url, title=page["title"],
                    skip_reason=f"尺寸 {width}×{height}，小于 {min_side}px，判为图标/logo",
                )
                continue

            yield CollectItem(
                data=data,
                filename=filename,
                title=page["title"],
                author=page["author"],
                source_url=page_url,
                text=page["text"],
                hashtags=page["hashtags"],
                shot_date=page.get("published_at", ""),
            )


# ---------- 工具函数 ----------
def _to_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _looks_like_image(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith((".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"))


def _download(url: str) -> bytes:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, stream=True)
    resp.raise_for_status()
    chunks, size = [], 0
    for chunk in resp.iter_content(1 << 16):
        size += len(chunk)
        if size > MAX_BYTES:
            raise ValueError("图片超过 20MB 上限")
        chunks.append(chunk)
    if not chunks:
        raise ValueError("返回内容为空")
    return b"".join(chunks)


def fetch_page(url: str) -> str:
    """抓页面 HTML。采集与"作者订正"共用这一个入口。"""
    return requests.get(url, headers=HEADERS, timeout=TIMEOUT).text


def fetch_page_meta(url: str) -> dict:
    """只抓页面元信息，返回 _page_meta 的字典。

    给 core.library 订正已入库样片用 —— 不用重新下载图片。
    """
    return _page_meta(fetch_page(url))


def download_image(url: str) -> bytes:
    """下载单张图片字节。页面采集与「关注博主」自动采集共用这一个入口。"""
    return _download(url)


def parse_page(html: str, page_url: str) -> tuple:
    """解析已经拿到的 HTML，返回 (元信息, 候选图地址)。

    浏览器通路把渲染后的 HTML 交给这里，避免页面上再写一套抽取/过滤逻辑 ——
    UI 元素（logo/图标/占位图）的剔除规则只有这一份。
    """
    return _page_meta(html), _extract_images(html, page_url)


def _page_meta(html: str) -> dict:
    """取页面标题、作者、正文文案与话题标签。

    作者取不到宁可为空，也不能拿 og:site_name 顶替 —— 那是站点名（"小红书"），
    不是博主名，之前就是这里导致所有样片的作者都变成了平台名。

    正文和话题标签分开返回：标签是作者亲手打的（小红书放在 keywords meta 里），
    正文里常混着营销文案，自动分类时要以标签为准，正文只作兜底。
    """
    soup = BeautifulSoup(html, "html.parser")

    def meta(prop: str) -> str:
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        return (tag.get("content") or "").strip() if tag else ""

    site_name = meta("og:site_name")
    title = meta("og:title") or (soup.title.string.strip() if soup.title and soup.title.string else "")
    # 去掉标题末尾的平台后缀，如 "如何引导拍摄 - 小红书"
    if site_name and title.endswith(site_name):
        title = title[: -len(site_name)].rstrip(" -—|·").strip()

    author = (
        _jsonld_author(soup)
        or meta("author")
        or meta("article:author")
        or meta("og:article:author")
    )

    # description 一般比 og:description 完整，取长的那个
    desc = meta("description")
    og_desc = meta("og:description")
    text = desc if len(desc) >= len(og_desc) else og_desc

    # 原帖发布日期。小红书优先取页面数据里的 time —— 它的 ld+json datePublished
    # 写的是服务器生成页面的时间（实测整站都是抓取当天），拿来做发布日期会全错。
    published = _xhs_publish_time(html)
    if published:
        published_at = datetime.fromtimestamp(published / 1000).strftime("%Y-%m-%d")
    else:
        published_at = _iso_date(
            _jsonld_value(soup, "datePublished")
            or meta("article:published_time")
            or meta("og:published_time")
            or meta("datePublished")
            or meta("pubdate")
        )

    return {
        "title": title[:80],
        "author": author[:40],
        "text": text[:2000],
        "hashtags": meta("keywords")[:500],
        "published_at": published_at,
    }


def _xhs_publish_time(html: str) -> int:
    """从小红书页面内嵌数据里取笔记发布时间（毫秒），取不到返回 0。

    只在 noteDetailMap 之后找，避开页面别处的 time 字段；SSR 里正常只有笔记这一处。
    """
    start = html.find('"noteDetailMap"')
    scope = html[start:] if start >= 0 else html
    match = re.search(r'"time"\s*:\s*(\d{13})', scope)
    return int(match.group(1)) if match else 0


def _iso_date(value: str) -> str:
    """把 ISO 时间（如 2026-10-19T14:12:20.882Z）转成本地时区的 YYYY-MM-DD。"""
    value = (value or "").strip()
    if not value:
        return ""
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo:
            moment = moment.astimezone()      # 统一到本机时区，避免跨零点差一天
        return moment.strftime("%Y-%m-%d")
    except Exception:
        return value[:10] if len(value) >= 10 else ""


def _jsonld_value(soup, key: str) -> str:
    """从 schema.org 结构化数据里取某个字段（如 datePublished）。"""
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        found = _find_value(data, key)
        if found:
            return str(found)
    return ""


def _find_value(node, key: str) -> str:
    """在嵌套的 ld+json 结构里递归找字段值，找不到返回空串。"""
    if isinstance(node, list):
        for child in node:
            found = _find_value(child, key)
            if found:
                return found
        return ""
    if not isinstance(node, dict):
        return ""
    if node.get(key):
        return node[key]
    for value in node.values():
        if isinstance(value, (dict, list)):
            found = _find_value(value, key)
            if found:
                return found
    return ""


def _jsonld_author(soup) -> str:
    """从 schema.org 结构化数据里取作者名 —— 比 meta 标签准得多。"""
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        name = _find_author_name(data)
        if name:
            return name
    return ""


def _find_author_name(node) -> str:
    """在 ld+json 结构里找 author 的名字，找不到返回空串。"""
    if isinstance(node, list):
        for child in node:
            found = _find_author_name(child)
            if found:
                return found
        return ""
    if not isinstance(node, dict):
        return ""

    author = node.get("author")
    if isinstance(author, dict) and author.get("name"):
        return str(author["name"]).strip()
    if isinstance(author, list):
        for one in author:
            if isinstance(one, dict) and one.get("name"):
                return str(one["name"]).strip()
    if isinstance(author, str) and author.strip():
        return author.strip()

    for key in ("@graph", "mainEntity", "itemListElement"):
        if key in node:
            found = _find_author_name(node[key])
            if found:
                return found
    return ""


def _extract_images(html: str, page_url: str) -> list:
    """按出现顺序抽取候选图，去重并剔除明显的 UI 元素。"""
    soup = BeautifulSoup(html, "html.parser")
    raw = [m.get("content", "") for m in soup.find_all("meta", attrs={"property": "og:image"})]
    for img in soup.find_all("img"):
        raw.append(img.get("src") or img.get("data-src") or img.get("data-original") or "")
        width = _to_int(img.get("width") or img.get("data-width"), 0)
        height = _to_int(img.get("height") or img.get("data-height"), 0)
        # 页面自己声明了尺寸的，随地址一起带上，供下一步判断
        raw[-1] = (raw[-1], width, height)

    seen, result = set(), []
    for entry in raw:
        if isinstance(entry, tuple):
            src, width, height = entry
        else:
            src, width, height = entry, 0, 0
        src = (src or "").strip()
        if not src or src.startswith("data:"):
            continue
        full = urljoin(page_url, src)
        if full in seen or _is_ui_element(full, width, height):
            continue
        seen.add(full)
        result.append(full)
    return result


def _is_ui_element(url: str, width: int, height: int) -> bool:
    """判断这张图是不是界面元素（logo / 图标 / 占位图）或表情包，而不是样片。"""
    lowered = url.lower()
    if any(key in lowered for key in UI_IMAGE_KEYS):
        return True

    # 表情包 / 梗图 / gif 动图：地址里有线索的提前剔掉，剩下的下载后再看文件头
    if looks_like_meme(lowered):
        return True

    host = urlparse(url).netloc.lower()
    if any(skip in host for skip in UI_ASSET_HOSTS):
        return True

    # 页面明确声明了宽高，且明显偏小 → 图标。只声明单边的不敢判，交给下载后的兜底
    if width and height and min(width, height) < WEB_MIN_SIDE:
        return True
    return False


def _platform_hint(url: str) -> str:
    host = urlparse(url).netloc.lower()
    for key, note in PLATFORM_HANDLERS.items():
        if key in host:
            return f"（该平台专属解析尚未接入：{note}）"
    return ""
