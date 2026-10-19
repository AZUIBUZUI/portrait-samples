# -*- coding: utf-8 -*-
"""REST API：给前端页面用，也可单独被脚本调用。"""
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, request

from config import AI_ENABLED
from core import library, watch
from core.collectors import list_collectors
from core.storage import guess_filename

api = Blueprint("api", __name__)


# ---------- 小工具 ----------
def _body() -> dict:
    return request.get_json(silent=True) or {}


def _int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _with_urls(item: dict) -> dict:
    """给样片补上前端直接可用的媒体地址。"""
    item["original_url"] = f"/media/original/{item['rel_path']}"
    item["thumb_url"] = f"/media/thumb/{item['thumb_path']}" if item.get("thumb_path") else ""
    return item


# ============================================================
# 状态 / 统计
# ============================================================
@api.get("/health")
def health():
    return jsonify({
        "ok": True,
        "collectors": list_collectors(),
        "ai_enabled": AI_ENABLED,
    })


@api.get("/stats")
def stats():
    return jsonify(library.stats())


# ============================================================
# 样片
# ============================================================
@api.get("/samples")
def list_samples():
    args = request.args
    data = library.list_samples(
        category_id=_int(args.get("category_id")),
        tag=args.get("tag", ""),
        source_type=args.get("source_type", ""),
        keyword=args.get("q", ""),
        group_key=args.get("group_key", ""),
        favorite=args.get("favorite") in ("1", "true"),
        page=_int(args.get("page"), 1) or 1,
        page_size=min(_int(args.get("page_size"), 60) or 60, 200),
        sort=args.get("sort", "newest"),
        seed=_int(args.get("seed"), 0) or 0,
    )
    data["items"] = [_with_urls(i) for i in data["items"]]
    return jsonify(data)


@api.get("/groups")
def list_groups():
    """分组视图：网页采集按原帖成组，本地导入按文件夹成组。"""
    return jsonify(library.list_groups(category_id=_int(request.args.get("category_id"))))


@api.get("/samples/<int:sample_id>")
def get_sample(sample_id: int):
    item = library.get_sample(sample_id)
    if not item:
        return jsonify({"error": "样片不存在"}), 404
    return jsonify(_with_urls(item))


@api.get("/samples/<int:sample_id>/neighbors")
def sample_neighbors(sample_id: int):
    """大图模式同组翻页：返回组内全部 id 与当前下标。"""
    return jsonify(library.group_neighbors(sample_id))


@api.patch("/samples/<int:sample_id>")
def update_sample(sample_id: int):
    body = _body()
    kwargs = {k: body[k] for k in ("category_id", "is_favorite", "note", "shot_date", "title") if k in body}
    if "tags" in body:
        kwargs["tags"] = body["tags"]
    library.update_sample(sample_id, **kwargs)
    return jsonify({"ok": True})


@api.delete("/samples/<int:sample_id>")
def delete_sample(sample_id: int):
    if not library.delete_sample(sample_id):
        return jsonify({"error": "样片不存在"}), 404
    return jsonify({"ok": True})


@api.post("/samples/<int:sample_id>/tags")
def set_tags(sample_id: int):
    tags = _body().get("tags", [])
    library.set_sample_tags(sample_id, tags)
    return jsonify({"ok": True})


# ============================================================
# 采集
# ============================================================
@api.post("/upload")
def upload():
    """手工上传 / 拖拽上传：multipart，字段名 files（可多选）。"""
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "没有收到文件"}), 400
    category_id = _int(request.form.get("category_id"))
    # 同一批上传的图片算一组
    batch = f"upload:{datetime.now():%Y-%m-%d %H:%M}"

    added = duplicated = failed = 0
    errors = []
    for file in files:
        name = guess_filename(file.filename or "upload.jpg")
        result = library.ingest_bytes(
            file.read(), name, category_id=category_id,
            source_type="manual", title=Path(name).stem, group_key=batch,
        )
        if result["status"] == "added":
            added += 1
        elif result["status"] == "duplicated":
            duplicated += 1
        else:
            failed += 1
            errors.append(f"{name}: {result.get('reason', '导入失败')}")

    return jsonify({"total": len(files), "succeeded": added,
                    "duplicated": duplicated, "failed": failed,
                    "message": "; ".join(errors[:3])})


@api.post("/collect")
def collect():
    """按采集来源跑一次采集任务。body: {source_type, target, category_id}"""
    body = _body()
    source_type = body.get("source_type", "")
    target = (body.get("target") or "").strip()
    if not target:
        return jsonify({"error": "请填写文件夹路径或网页地址"}), 400
    options = body.get("options") or {}
    if not isinstance(options, dict):
        options = {}
    try:
        result = library.run_collection(
            source_type, target, _int(body.get("category_id")), **options
        )
    except KeyError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@api.get("/collectors")
def collectors():
    return jsonify(list_collectors())


@api.get("/jobs")
def jobs():
    return jsonify(library.list_jobs(limit=_int(request.args.get("limit"), 20) or 20))


# ============================================================
# 关注博主：把新作品补进素材库（页面按钮 / 命令行触发）
# ============================================================
@api.get("/watchers")
def list_watchers():
    return jsonify(watch.list_watchers())


@api.post("/watchers")
def add_watcher():
    body = _body()
    result = watch.add_watcher(body.get("url", ""), body.get("name", ""))
    return jsonify(result), (200 if result.get("ok") else 400)


@api.patch("/watchers/<int:watcher_id>")
def update_watcher(watcher_id: int):
    body = _body()
    kwargs = {k: body[k] for k in ("name", "url", "enabled") if k in body}
    return jsonify(watch.update_watcher(watcher_id, **kwargs))


@api.delete("/watchers/<int:watcher_id>")
def delete_watcher(watcher_id: int):
    if not watch.delete_watcher(watcher_id):
        return jsonify({"error": "该博主不存在"}), 404
    return jsonify({"ok": True})


@api.post("/watchers/run")
def run_watchers():
    """跑一遍关注列表（不传 id 就跑全部启用的博主）。页面按钮与命令行都走这里。"""
    body = _body()
    ids = body.get("watcher_ids")
    if isinstance(ids, int):
        ids = [ids]
    if not isinstance(ids, list):
        ids = None
    return jsonify(watch.run_watchers(
        watcher_ids=ids,
        limit=min(_int(body.get("limit"), 30) or 30, 200),
        headless=body.get("headless", True) is not False,
    ))


@api.get("/watchers/notes")
def watched_notes():
    return jsonify(watch.list_seen_notes(limit=_int(request.args.get("limit"), 30) or 30))


# ============================================================
# 自动分类
# ============================================================
@api.post("/classify/auto")
def auto_classify():
    only_unlabeled = _body().get("only_unlabeled", True)
    return jsonify(library.auto_classify(only_unlabeled=bool(only_unlabeled)))


# ============================================================
# 人工纠正 → 训练 → 分类
# ============================================================
@api.get("/label/pending")
def label_pending():
    data = library.labeling_pending()
    data["items"] = [_with_urls(item) for item in data["items"]]
    return jsonify(data)


@api.post("/label/train")
def label_train():
    return jsonify(library.train_and_classify(k=_int(_body().get("k"), 5) or 5))


@api.post("/label/seed")
def label_seed():
    limit = _int(_body().get("limit"), 60) or 60
    return jsonify(library.seed_labeling_batch(limit=min(max(limit, 1), 200)))


# ============================================================
# 分类 / 标签
# ============================================================
@api.get("/categories")
def list_categories():
    return jsonify(library.list_categories())


@api.post("/categories")
def create_category():
    body = _body()
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "分类名不能为空"}), 400
    try:
        result = library.create_category(
            name, body.get("keywords", ""), body.get("color", "#6366f1"),
            body.get("description", ""), _int(body.get("parent_id")),
        )
    except Exception as exc:
        return jsonify({"error": f"创建失败: {exc}"}), 400
    return jsonify(result)


@api.patch("/categories/<int:category_id>")
def update_category(category_id: int):
    library.update_category(category_id, **_body())
    return jsonify({"ok": True})


@api.delete("/categories/<int:category_id>")
def delete_category(category_id: int):
    library.delete_category(category_id)
    return jsonify({"ok": True})


@api.get("/tags")
def list_tags():
    return jsonify(library.list_tags())


# ============================================================
# 维护
# ============================================================
@api.post("/maintenance/fix-web-meta")
def fix_web_meta():
    """重新打开原帖，订正已采集样片的作者与标题。

    早期版本把 og:site_name（站点名，如"小红书"）当作者兜底，导致整库作者写错。
    """
    return jsonify(library.repair_web_metadata())


@api.post("/maintenance/apply-standard")
def apply_standard():
    """换成写真「画面状态」分类标准：删旧分类 → 建新 → 重跑归类 → 同步话题标签。

    会清空现有分类与已有归类，图片不动。
    """
    return jsonify(library.apply_category_standard())


@api.post("/maintenance/review-samples")
def review_samples():
    """按写真文字标准回审已入库的网页采集样片：明确不是的打「非人像」，拿不准的打「待确认」。

    导入闸是后加的，这条用来把之前采进来的也过一遍。只标记，不删图。
    """
    return jsonify(library.review_imported_samples())
