# -*- coding: utf-8 -*-
"""业务服务层：导入落库、自动分类、检索、任务记录、分类/标签维护。

上层（路由）只调用这里的函数，不直接写 SQL。
"""
from datetime import datetime
from pathlib import Path

from core import storage, visual
from core.collectors import CollectItem, fetch_page_meta, get_collector
from core.db import DEFAULT_CATEGORIES, get_conn


# ============================================================
# 零、导入闸：按原帖文字判断"是不是人像写真"
# ============================================================
# 只拦**带原帖文字**的通路（网页采集、关注博主）；本地文件夹导入和手工上传没有帖子文案，
# 是你自己挑的图，不在这里拦。
#
# 命中「写真相关词」→ 收下；命中「非人像摄影词」→ 不收；两边都没命中 → 存疑：
# 照样收下，但写上备注并打「待确认」标签，你在图库按标签筛出来自己定。
# 判断只看标题 + 作者自己打的话题标签 —— 正文里混着广告和拍摄讲解，
# 之前就踩过它把整组图带偏的坑（见 _classify_text 的注释）。
PORTRAIT_WORDS = (
    "写真", "人像", "约拍", "肖像", "客片", "模特", "出镜", "少女",
    "港风", "婚纱", "个人写真", "人像摄影", "氛围感人像", "portrait",
)
NOT_PORTRAIT_WORDS = (
    "美食", "食谱", "探店", "咖啡", "烘焙", "甜品", "穿搭", "服饰", "护肤",
    "美妆", "彩妆", "开箱", "测评", "好物", "带货", "优惠", "团购",
    "宠物", "家居", "装修", "数码", "壁纸", "表情包", "梗图", "斗图", "meme",
    "插画", "手绘", "ai绘画", "风景", "风光", "星空", "旅游攻略", "酒店",
    "民宿", "露营", "健身", "减脂", "vlog", "海报", "截图",
)
PENDING_TAG = "待确认"
PENDING_NOTE = "待确认：原帖文字里没有明确的写真线索，先收下，请人工看一眼"
NOT_PORTRAIT_TAG = "非人像"
NOT_PORTRAIT_NOTE = "按原帖文字判定不是人像写真：{why}（图片没删，你自己定去留）"


def judge_portrait(title: str, hashtags: str = "") -> tuple:
    """按原帖文字判断这篇作品是不是人像写真，返回 (verdict, reason)。

    verdict：keep（是，收下） / skip（明确不是，不导入） / unsure（拿不准，先收下并标记）

    **先看写真词再看排除词**：探店写真、咖啡馆人像这类"场景型写真"不该被场景词误杀；
    反过来"美食摄影"这种正文里没有写真词的，会被排除词挡住。
    """
    text = f"{title or ''} {hashtags or ''}".strip().lower()
    if not text:
        return "unsure", "没有可判断的文字"
    for word in PORTRAIT_WORDS:
        if word in text:
            return "keep", f"命中写真相关词：{word}"
    for word in NOT_PORTRAIT_WORDS:
        if word in text:
            return "skip", f"命中非人像摄影词：{word}"
    return "unsure", "文字里没有明确的写真线索"


def tag_names(raw: str) -> list:
    """把"逗号分隔的话题标签"拆成标签名列表。"""
    return [t.strip() for t in (raw or "").replace("，", ",").split(",") if t.strip()]


# ============================================================
# 一、入库
# ============================================================
def ingest_path(
    abs_path: str,
    category_id: int | None = None,
    source_type: str = "local",
    source_url: str = "",
    author: str = "",
    title: str = "",
    source_text: str = "",
    source_hashtags: str = "",
    shot_date: str = "",
    group_key: str = "",
) -> dict:
    """把一张本地图片导入库中，返回 {status, sample_id, reason}。

    status: added / duplicated / failed
    """
    path = Path(abs_path)
    try:
        digest = storage.file_hash(path)
    except Exception as exc:
        return {"status": "failed", "reason": f"读取失败: {exc}"}

    with get_conn() as conn:
        existed = conn.execute("SELECT id FROM samples WHERE file_hash = ?", (digest,)).fetchone()
    if existed:
        return {"status": "duplicated", "sample_id": existed["id"]}

    try:
        rel_path, stored = storage.store_file(path, shot_date)
        thumb_path, width, height, exif_date = storage.make_thumb(stored, rel_path)
    except Exception as exc:
        return {"status": "failed", "reason": f"入库失败: {exc}"}

    shot = shot_date or exif_date
    try:
        return _insert_sample(
            digest=digest, rel_path=rel_path, thumb_path=thumb_path,
            filename=path.name, width=width, height=height,
            file_size=path.stat().st_size, source_type=source_type,
            source_url=source_url, author=author or path.parent.name,
            title=title or path.stem, shot_date=shot, category_id=category_id,
            group_key=group_key, source_text=source_text,
            source_hashtags=source_hashtags,
        )
    except Exception as exc:
        return {"status": "failed", "reason": f"写入数据库失败: {exc}"}


def ingest_bytes(
    data: bytes,
    filename: str,
    category_id: int | None = None,
    source_type: str = "manual",
    source_url: str = "",
    author: str = "",
    title: str = "",
    source_text: str = "",
    source_hashtags: str = "",
    group_key: str = "",
    note: str = "",
    shot_date: str = "",
) -> dict:
    """把内存中的图片字节导入库中（手工上传 / 网页下载）。

    shot_date 传原帖发布日期；没有就退回图片 EXIF 的拍摄时间。
    """
    import hashlib

    digest = hashlib.sha256(data).hexdigest()
    with get_conn() as conn:
        existed = conn.execute("SELECT id FROM samples WHERE file_hash = ?", (digest,)).fetchone()
    if existed:
        return {"status": "duplicated", "sample_id": existed["id"]}

    try:
        rel_path, stored = storage.store_bytes(data, filename)
        thumb_path, width, height, exif_date = storage.make_thumb(stored, rel_path)
    except Exception as exc:
        return {"status": "failed", "reason": f"入库失败: {exc}"}

    try:
        return _insert_sample(
            digest=digest, rel_path=rel_path, thumb_path=thumb_path,
            filename=filename, width=width, height=height, file_size=len(data),
            source_type=source_type, source_url=source_url, author=author,
            title=title or Path(filename).stem, shot_date=shot_date or exif_date,
            category_id=category_id, group_key=group_key, source_text=source_text,
            source_hashtags=source_hashtags, note=note,
        )
    except Exception as exc:
        return {"status": "failed", "reason": f"写入数据库失败: {exc}"}


def _insert_sample(**kw) -> dict:
    """写入 samples 表；只有话题标签 / 标题能命中时才自动分类。

    画面特征的启发式猜测已经停用：新图在人工纠正出训练集之前保持「未分类」。
    """
    if kw.get("category_id") is None:
        cats = _category_rows()
        kw["category_id"] = (_match_in_categories(cats, kw.get("source_hashtags") or "")
                             or _match_in_categories(cats, _classify_text(kw)))
        kw["auto_labeled"] = 1 if kw["category_id"] else 0
    kw.setdefault("auto_labeled", 0)

    columns = [
        "file_hash", "rel_path", "thumb_path", "filename", "width", "height",
        "file_size", "source_type", "source_url", "source_author", "title",
        "shot_date", "category_id", "auto_labeled", "group_key", "source_text",
        "source_hashtags", "note",
    ]
    kw["file_hash"] = kw.pop("digest", "")
    kw["source_author"] = kw.pop("author", "")
    kw.setdefault("group_key", "")
    kw.setdefault("source_text", "")
    kw.setdefault("source_hashtags", "")
    kw.setdefault("note", "")
    values = [kw.get(c) for c in columns]
    with get_conn() as conn:
        cur = conn.execute(
            f"INSERT INTO samples({','.join(columns)}) VALUES ({','.join('?' * len(columns))})",
            values,
        )
        conn.commit()
        return {"status": "added", "sample_id": cur.lastrowid}


# ============================================================
# 二、采集任务
# ============================================================
def start_job(source_type: str, target: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO jobs(source_type, target, status) VALUES (?,?, 'running')",
            (source_type, target),
        )
        conn.commit()
        return cur.lastrowid


def finish_job(job_id: int, status: str, total: int, succeeded: int,
               duplicated: int, failed: int, message: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            """UPDATE jobs SET status=?, total=?, succeeded=?, duplicated=?, failed=?, message=?
               WHERE id=?""",
            (status, total, succeeded, duplicated, failed, message[:500], job_id),
        )
        conn.commit()


def run_collection(source_type: str, target: str, category_id: int | None = None, **options) -> dict:
    """跑一次采集：本地文件夹导入 / 网页采集 共用这条流水线。

    options 会原样透传给采集器（例如 local 的 recursive、web 的 max_images / min_side）。
    被判定为 UI 元素（logo / 图标）而丢掉的条目计入 skipped，不算失败，但会写进任务备注。
    """
    job_id = start_job(source_type, target)
    collector = get_collector(source_type)

    total = succeeded = duplicated = failed = skipped = 0
    errors, skips = [], []
    for item in collector.run(target, **options):   # type: ignore[arg-type]
        # 分组键：网页采集按原帖成组，本地导入按文件夹成组
        group_key = item.source_url or f"{source_type}:{target}"
        result = _ingest_item(item, category_id, group_key)
        item_status = result["status"]
        if item_status == "skipped":
            skipped += 1
            skips.append(result.get("reason", "非样片"))
            continue
        total += 1
        if item_status == "added":
            succeeded += 1
        elif item_status == "duplicated":
            duplicated += 1
        else:
            failed += 1
            errors.append(result.get("reason", "未知错误"))

    notes = []
    if skipped:
        notes.append(f"已过滤 {skipped} 张非样片（例：{skips[0]}）")
    notes.extend(errors[:3])
    message = "; ".join(notes)

    if total == 0:
        status = "failed"
        message = message or "没有采集到任何素材"
    elif succeeded == 0 and failed > 0:
        status = "failed"
    else:
        status = "done"

    finish_job(job_id, status, total, succeeded, duplicated, failed, message)
    return {
        "job_id": job_id, "status": status, "total": total,
        "succeeded": succeeded, "duplicated": duplicated, "failed": failed,
        "skipped": skipped, "message": message,
    }


def _ingest_item(item: CollectItem, category_id: int | None, group_key: str = "") -> dict:
    if item.error:
        return {"status": "failed", "reason": item.error}
    if item.skip_reason:
        return {"status": "skipped", "reason": item.skip_reason}
    if item.data is not None:
        # 带原帖文字的（整页采集）先过一道"是不是人像写真"；直链图片没有文字，不拦
        verdict, why = "keep", ""
        if item.title or item.hashtags:
            verdict, why = judge_portrait(item.title, item.hashtags)
            if verdict == "skip":
                return {"status": "skipped", "reason": f"{why}｜{item.title[:24] or item.hashtags[:24]}"}

        result = ingest_bytes(
            item.data, item.filename, category_id=category_id,
            source_type="web", source_url=item.source_url,
            author=item.author, title=item.title,
            group_key=group_key, source_text=item.text,
            source_hashtags=item.hashtags,
            note="" if verdict == "keep" else PENDING_NOTE,
            shot_date=item.shot_date,
        )
        if result["status"] == "added" and verdict == "unsure":
            set_sample_tags(result["sample_id"], tag_names(item.hashtags) + [PENDING_TAG])
        return result
    if item.path:
        return ingest_path(
            item.path, category_id=category_id, source_type="local",
            source_url=item.source_url, author=item.author,
            title=item.title, shot_date=item.shot_date,
            group_key=group_key, source_text=item.text,
            source_hashtags=item.hashtags,
        )
    return {"status": "failed", "reason": "空素材"}


def list_jobs(limit: int = 20) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def repair_web_metadata() -> dict:
    """订正网页采集样片的作者、标题、正文文案、话题标签与发布日期，并重跑一次自动分类。

    早期版本把 og:site_name 当作者兜底，而那是站点名（"小红书"），
    导致整库 source_author 都写成了平台名。这里按原帖地址各抓一次元信息改回来，
    每个原帖只请求一次，且不重新下载图片。补齐后重跑分类，老样片也能用上标签。
    日期统一改写成原帖的发布日期（之前网页样片的日期是空的）。
    """
    with get_conn() as conn:
        groups = [dict(r) for r in conn.execute(
            """SELECT source_url, COUNT(*) AS total FROM samples
               WHERE source_type = 'web' AND source_url <> ''
               GROUP BY source_url"""
        ).fetchall()]

    updated = failed = 0
    applied, details = [], []
    for group in groups:
        url = group["source_url"]
        try:
            page = fetch_page_meta(url)
        except Exception as exc:
            failed += group["total"]
            details.append(f"{url[:70]} 打开失败：{exc}")
            continue

        fields, params = [], []
        if page["author"]:
            fields.append("source_author = ?")
            params.append(page["author"])
        if page["title"]:
            fields.append("title = ?")
            params.append(page["title"])
        if page["text"]:
            fields.append("source_text = ?")
            params.append(page["text"])
        if page["hashtags"]:
            fields.append("source_hashtags = ?")
            params.append(page["hashtags"])
        if page.get("published_at"):
            fields.append("shot_date = ?")
            params.append(page["published_at"])
        if not fields:
            failed += group["total"]
            details.append(f"{url[:70]} 没取到作者/标题/文案（可能需要登录，或链接已失效）")
            continue

        with get_conn() as conn:
            cur = conn.execute(
                f"UPDATE samples SET {', '.join(fields)} "
                "WHERE source_type = 'web' AND source_url = ?",
                params + [url],
            )
            conn.commit()
            changed = cur.rowcount
        updated += changed
        applied.append(f"{page['author'] or '未取到作者'} · {changed} 张")

    # 分类依据变了（现在以话题标签为准），把之前自动归类的重跑一遍；人工改过的不动
    if updated:
        with get_conn() as conn:
            conn.execute(
                "UPDATE samples SET category_id = NULL, auto_labeled = 0 WHERE auto_labeled = 1"
            )
            conn.commit()
    classified = auto_classify(only_unlabeled=True)

    return {"notes": len(groups), "updated": updated, "failed": failed,
            "applied": applied, "details": details,
            "classified": classified["labeled"]}


def review_imported_samples() -> dict:
    """按同一套写真文字标准，对**已入库的网页采集样片**回审一遍（只标记，不删图）。

    导入闸是后加的，之前采进来的可能夹着非人像内容（博主发的风景、头像、带货、教学长图…）。
    这里按原帖分组重判一次：明确不是的打「非人像」标签，拿不准的打「待确认」标签，
    判为写真的把这两种旧标记清掉。图片文件一张不删 —— 去留在你自己手上。
    本地导入与手工上传没有帖子文案，不参与回审。
    """
    with get_conn() as conn:
        groups = [dict(r) for r in conn.execute(
            """SELECT source_url,
                      MAX(title)           AS title,
                      MAX(source_hashtags) AS tags,
                      COUNT(*)             AS total,
                      GROUP_CONCAT(id)     AS ids
               FROM samples
               WHERE source_type = 'web' AND source_url <> ''
               GROUP BY source_url"""
        ).fetchall()]

    images = {"keep": 0, "unsure": 0, "skip": 0}
    counts = {"groups": len(groups), "keep": 0, "unsure": 0, "skip": 0}
    for group in groups:
        verdict, why = judge_portrait(group["title"], group["tags"])
        counts[verdict] += 1
        ids = [int(x) for x in (group["ids"] or "").split(",") if x]
        images[verdict] += len(ids)

        note = {"skip": NOT_PORTRAIT_NOTE.format(why=why),
                "unsure": PENDING_NOTE}.get(verdict, "")
        with get_conn() as conn:
            for sample_id in ids:
                conn.execute("UPDATE samples SET note = ? WHERE id = ?", (note, sample_id))
                _apply_review_tag(conn, sample_id, verdict)
            conn.commit()

    # 清掉没人用的标签，免得标签列表里堆一堆一遍性标记
    with get_conn() as conn:
        conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM sample_tags)")
        conn.commit()

    return {**counts, "images": images}


def _apply_review_tag(conn, sample_id: int, verdict: str) -> None:
    """写回审结果：明确不是 → 非人像；拿不准 → 待确认；判为写真 → 清掉这两个标记。"""
    for name in (PENDING_TAG, NOT_PORTRAIT_TAG):
        conn.execute(
            "DELETE FROM sample_tags WHERE sample_id = ? "
            "AND tag_id = (SELECT id FROM tags WHERE name = ?)", (sample_id, name)
        )
    tag = NOT_PORTRAIT_TAG if verdict == "skip" else PENDING_TAG if verdict == "unsure" else ""
    if not tag:
        return
    conn.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (tag,))
    tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (tag,)).fetchone()["id"]
    conn.execute("INSERT OR IGNORE INTO sample_tags(sample_id, tag_id) VALUES (?,?)", (sample_id, tag_id))


def list_groups(category_id: int | None = None) -> dict:
    """按分组键（网页采集=原帖，本地=文件夹，上传=批次）汇总，供"分组视图"用。"""
    where, params = ["s.group_key <> ''"], []
    if category_id is not None:
        where.append("s.category_id = ?")
        params.append(category_id)

    with get_conn() as conn:
        rows = conn.execute(
            f"""SELECT s.group_key,
                       COUNT(*)            AS total,
                       MAX(s.source_type)  AS source_type,
                       MAX(s.title)        AS title,
                       MAX(s.source_author) AS author,
                       MAX(s.source_url)   AS source_url,
                       MIN(s.id)           AS first_id,
                       MAX(s.created_at)   AS created_at,
                       (SELECT s2.thumb_path FROM samples s2
                         WHERE s2.group_key = s.group_key AND s2.thumb_path <> ''
                         ORDER BY s2.id LIMIT 1) AS cover_thumb,
                       (SELECT s2.rel_path FROM samples s2
                         WHERE s2.group_key = s.group_key
                         ORDER BY s2.id LIMIT 1) AS cover_path
                FROM samples s
                WHERE {' AND '.join(where)}
                GROUP BY s.group_key
                ORDER BY first_id DESC""",
            params,
        ).fetchall()
        ungrouped = conn.execute(
            "SELECT COUNT(*) FROM samples WHERE group_key = ''"
        ).fetchone()[0]

    items = []
    for row in rows:
        data = dict(row)
        data["label"] = _group_label(data)
        data["cover_url"] = (
            f"/media/thumb/{data['cover_thumb']}" if data["cover_thumb"]
            else f"/media/original/{data['cover_path']}"
        )
        items.append(data)
    return {"total": len(items), "ungrouped": ungrouped, "items": items}


def _group_label(group: dict) -> str:
    """分组卡片上显示的名字。"""
    if group.get("source_type") == "web":
        return group.get("title") or "未命名笔记"
    key = group.get("group_key") or ""
    return key.split(":", 1)[-1] if ":" in key else key


# ============================================================
# 三、自动分类
# ============================================================
# 写真「画面状态」分类的关键词只描述**画面状态**（安静、动感…）。
# 风格 / 光线词（日系、逆光、胶片、电影感、氛围感…）不要写进分类关键词 ——
# 它们是可多选的属性，放在样片标签里，混进分类只会让整组图被误归到一类。
# 预设定义在 core/db.py 的 DEFAULT_CATEGORIES（唯一来源，这里只导入）。


def _classify_text(sample: dict) -> str:
    """兜底匹配文本：标题 / 文件名 / 作者。

    故意不包含正文文案 —— 正文多是营销话术或拍摄讲解，和"画面状态"无关，
    放进来只会把广告里的词（比如"治愈感"）当成分类依据，把整组图带偏。
    正文仍然会存下来（弹窗里看得到），只是不参与分类。
    """
    parts = [sample.get("title") or "", sample.get("filename") or "",
             sample.get("author") or "", sample.get("source_author") or ""]
    return " ".join(parts)


def match_category(hashtags: str, text: str = "") -> int | None:
    """按分类关键词归类，命中第一个即返回其 id。

    两级：先拿**作者自己打的话题标签**匹配，命中就定下；没命中再退回正文 / 标题。
    标签是作者亲手写的，正文（尤其带广告的）里常混着不相关的词，容易误命中 ——
    比如一篇手机广告的文案里有"治愈感"，就会把整组图错归到"日系清新"。
    """
    cats = _category_rows()
    return _match_in_categories(cats, hashtags) or _match_in_categories(cats, text)


def _category_rows() -> list:
    """一次读出全部分类，批量分类时复用，避免每条都开一次库。"""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, name, keywords FROM categories ORDER BY sort_order, id"
        ).fetchall()]


def _match_in_categories(cats: list, text: str) -> int | None:
    if not text:
        return None
    text = text.lower()
    for cat in cats:
        for kw in _split_keywords(cat["keywords"]):
            if kw in text:
                return cat["id"]
    return None


def _split_keywords(raw: str) -> list:
    return [k.strip().lower() for k in (raw or "").replace("，", ",").split(",") if k.strip()]


# 画面特征分类的结果 → 分类名。只有话题标签 / 文案一个词都没命中时才走这条路。
VISUAL_CATEGORY_NAMES = {
    "quiet": "安静",
    "energetic": "元气",
    "emotional": "安静",   # 情绪已并入安静，画面特征的 emotional 也归到同一类
    "cool": "酷拽",
}
FALLBACK_CATEGORY_NAME = "酷拽"


def _name_map(cats: list) -> dict:
    return {cat["name"]: cat["id"] for cat in cats}


def _visual_category(thumb_path: str, name_map: dict) -> int | None:
    """按画面特征猜一个最可能的分类；读不出图返回 None。"""
    if not thumb_path:
        return None
    key = visual.classify(storage.THUMBS_DIR / thumb_path)
    return name_map.get(VISUAL_CATEGORY_NAMES.get(key, ""))


def _fallback_category(cats: list, name_map: dict) -> int | None:
    """强制分类的最后一档：优先「酷拽」，没有就取排序第一的分类。"""
    return name_map.get(FALLBACK_CATEGORY_NAME) or (cats[0]["id"] if cats else None)


MIN_TRAIN_SAMPLES = 20   # 训练样本太少时准确率没有参考意义，先多纠正几张


def _human_pairs() -> list:
    """人工标注过的样本 → [(特征向量, 分类id)]。"""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT thumb_path, category_id FROM samples
               WHERE human_labeled = 1 AND category_id IS NOT NULL AND thumb_path <> ''"""
        ).fetchall()
    pairs = []
    for row in rows:
        vector = visual.feature_vector(storage.THUMBS_DIR / row["thumb_path"])
        if vector is not None:
            pairs.append((vector, int(row["category_id"])))
    return pairs


def _trained_model(k: int = 5) -> dict | None:
    """用人工纠正过的样本建最近邻模型；样本不够返回 None。"""
    pairs = _human_pairs()
    if len(pairs) < MIN_TRAIN_SAMPLES:
        return None
    train_x, labels = visual.prepare(pairs)
    return {"x": train_x, "labels": labels, "k": k, "samples": len(pairs)}


def _predict_category(model: dict, thumb_path: str) -> int | None:
    if not thumb_path:
        return None
    vector = visual.feature_vector(storage.THUMBS_DIR / thumb_path)
    if vector is None:
        return None
    return visual.knn_predict(vector, model["x"], model["labels"], model["k"])


def auto_classify(only_unlabeled: bool = True) -> dict:
    """给未分类样片归类：先看话题标签 / 文案，命不中再用人工纠正出的模型猜。

    没有人工资格样本时，文字也没命中的样片会保持「未分类」，不再用启发式乱猜。
    """
    with get_conn() as conn:
        sql = ("SELECT id, title, filename, source_author, source_text, "
               "source_hashtags, thumb_path FROM samples")
        if only_unlabeled:
            sql += " WHERE category_id IS NULL AND human_labeled = 0"
        rows = [dict(r) for r in conn.execute(sql).fetchall()]

    labeled, skipped = 0, 0
    cats = _category_rows()
    model = _trained_model()
    with get_conn() as conn:
        for row in rows:
            category_id = (_match_in_categories(cats, row["source_hashtags"] or "")
                           or _match_in_categories(cats, _classify_text(row)))
            if category_id is None and model:
                category_id = _predict_category(model, row["thumb_path"] or "")
            if category_id is None:
                skipped += 1
                continue
            conn.execute(
                "UPDATE samples SET category_id = ?, auto_labeled = 1 WHERE id = ?",
                (category_id, row["id"]),
            )
            labeled += 1
        conn.commit()
    return {"labeled": labeled, "skipped": skipped, "scanned": len(rows),
            "model_samples": model["samples"] if model else 0}


# ============================================================
# 三·五、人工纠正 → 训练 → 批量分类
# ============================================================
def seed_labeling_batch(limit: int = 60) -> dict:
    """抽一批待纠正样片（已有的机器猜测保留），供人工确认 / 修正。

    优先抽未分类的（先用画面特征给个初始猜测），也抽机器已经分好的，
    这样纠正完一批还能继续抽下一批，训练样本越攒越多。
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, thumb_path, category_id FROM samples
               WHERE needs_review = 0 AND human_labeled = 0
                 AND (category_id IS NULL OR auto_labeled = 1)
               ORDER BY RANDOM() LIMIT ?""", (limit,),
        ).fetchall()

    cats = _category_rows()
    name_map = _name_map(cats)
    fallback = _fallback_category(cats, name_map)
    assigned = 0
    with get_conn() as conn:
        for row in rows:
            if row["category_id"] is None:
                category_id = _visual_category(row["thumb_path"] or "", name_map) or fallback
                if category_id is None:
                    continue
                conn.execute(
                    "UPDATE samples SET category_id = ?, auto_labeled = 1, needs_review = 1 WHERE id = ?",
                    (category_id, row["id"]),
                )
            else:
                conn.execute("UPDATE samples SET needs_review = 1 WHERE id = ?", (row["id"],))
            assigned += 1
        conn.commit()
    return {"sampled": len(rows), "assigned": assigned}


def labeling_pending() -> dict:
    """人工纠正用的待确认样本（needs_review=1 且仍是机器猜测）与进度。"""
    with get_conn() as conn:
        remaining = conn.execute(
            "SELECT COUNT(*) FROM samples WHERE needs_review = 1 AND human_labeled = 0 AND auto_labeled = 1"
        ).fetchone()[0]
        confirmed = conn.execute(
            "SELECT COUNT(*) FROM samples WHERE needs_review = 1 AND human_labeled = 1"
        ).fetchone()[0]
        rows = conn.execute(
            """SELECT s.*, c.name AS category_name, c.color AS category_color
               FROM samples s LEFT JOIN categories c ON c.id = s.category_id
               WHERE s.needs_review = 1 AND s.human_labeled = 0 AND s.auto_labeled = 1
               ORDER BY s.id"""
        ).fetchall()
        items = [_with_tags(conn, r) for r in rows]
    return {"remaining": remaining, "confirmed": confirmed, "items": items}


def train_and_classify(k: int = 5) -> dict:
    """以人工纠正过的样本为训练集做最近邻分类，处理所有未确认的样片。

    目标 = 未分类的 + 之前机器猜过的（auto_labeled=1）。人工确认过的不会被动。
    """
    pairs = _human_pairs()
    if len(pairs) < MIN_TRAIN_SAMPLES:
        return {"ok": False,
                "error": f"人工纠正的样本还不够（当前 {len(pairs)} 张，至少 {MIN_TRAIN_SAMPLES} 张）"}

    accuracy = visual.loo_accuracy(pairs, k)
    train_x, labels = visual.prepare(pairs)
    model = {"x": train_x, "labels": labels, "k": k}

    cats = _category_rows()
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            """SELECT id, title, filename, source_author, source_text,
                      source_hashtags, thumb_path FROM samples
               WHERE human_labeled = 0 AND (category_id IS NULL OR auto_labeled = 1)"""
        ).fetchall()]
        predicted = from_text = skipped = 0
        for row in rows:
            category_id = (_match_in_categories(cats, row["source_hashtags"] or "")
                           or _match_in_categories(cats, _classify_text(row)))
            if category_id is not None:
                from_text += 1
            else:
                category_id = _predict_category(model, row["thumb_path"] or "")
                if category_id is None:
                    skipped += 1
                    continue
                predicted += 1
            conn.execute(
                "UPDATE samples SET category_id = ?, auto_labeled = 1 WHERE id = ?",
                (category_id, row["id"]),
            )
        # 这批人工纠正到此结束，后续模型预测不再进「待纠正」列表
        conn.execute("UPDATE samples SET needs_review = 0 WHERE needs_review = 1")
        conn.commit()

    return {"ok": True, "trained": len(pairs), "accuracy": accuracy,
            "targets": len(rows), "predicted": predicted,
            "from_text": from_text, "skipped": skipped}


# ============================================================
# 四、样片查询与编辑
# ============================================================
# 排序方式白名单：键 → SQL ORDER BY（空值统一沉底，避免"无日期"顶在最前）
# random 用乘法哈希伪造"稳定随机"：同一 seed 下每次查询顺序一致，
# 无限滚动翻页时才不会出现前后页重复 / 丢图。
_SORT_CLAUSES = {
    "newest":   "s.id DESC",
    "oldest":   "s.id ASC",
    "shot_new": "(s.shot_date = '') ASC, s.shot_date DESC, s.id DESC",
    "shot_old": "(s.shot_date = '') ASC, s.shot_date ASC, s.id ASC",
    "largest":  "(s.width * s.height) DESC, s.id DESC",
    "heaviest": "s.file_size DESC, s.id DESC",
    "author":   "(s.source_author = '') ASC, s.source_author COLLATE NOCASE ASC, s.id DESC",
    "random":   "((s.id * 2654435761 + {seed}) % 4294967296) ASC",
}


def list_samples(
    category_id: int | None = None,
    tag: str = "",
    source_type: str = "",
    keyword: str = "",
    group_key: str = "",
    favorite: bool = False,
    page: int = 1,
    page_size: int = 60,
    sort: str = "newest",
    seed: int = 0,
) -> dict:
    where, params = [], []
    if category_id is not None:
        where.append("s.category_id = ?")
        params.append(category_id)
    if source_type:
        where.append("s.source_type = ?")
        params.append(source_type)
    if group_key:
        where.append("s.group_key = ?")
        params.append(group_key)
    if favorite:
        where.append("s.is_favorite = 1")
    if keyword:
        where.append("(s.title LIKE ? OR s.filename LIKE ? OR s.source_author LIKE ? OR s.note LIKE ?)")
        params.extend([f"%{keyword}%"] * 4)
    if tag:
        where.append("s.id IN (SELECT st.sample_id FROM sample_tags st JOIN tags t ON t.id=st.tag_id WHERE t.name=?)")
        params.append(tag)

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    order = _SORT_CLAUSES.get(sort, _SORT_CLAUSES["newest"])
    order = order.replace("{seed}", str(max(0, int(seed or 0))))
    with get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM samples s {clause}", params).fetchone()[0]
        rows = conn.execute(
            f"""SELECT s.*, c.name AS category_name, c.color AS category_color
                FROM samples s LEFT JOIN categories c ON c.id = s.category_id
                {clause} ORDER BY {order} LIMIT ? OFFSET ?""",
            params + [page_size, (page - 1) * page_size],
        ).fetchall()
        items = [_with_tags(conn, r) for r in rows]
    return {"total": total, "page": page, "page_size": page_size,
            "sort": sort, "items": items}


def _with_tags(conn, row) -> dict:
    data = dict(row)
    data["tags"] = [
        r["name"] for r in conn.execute(
            """SELECT t.name FROM sample_tags st JOIN tags t ON t.id = st.tag_id
               WHERE st.sample_id = ? ORDER BY t.name""",
            (row["id"],),
        ).fetchall()
    ]
    return data


def get_sample(sample_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            """SELECT s.*, c.name AS category_name FROM samples s
               LEFT JOIN categories c ON c.id = s.category_id WHERE s.id = ?""",
            (sample_id,),
        ).fetchone()
        return _with_tags(conn, row) if row else None


def group_neighbors(sample_id: int) -> dict:
    """大图模式翻页用：返回该样片所在分组的全部 id 和它的下标。

    顺序跟图库列表一致（新→旧），这样点第一张看到的就是 1 / N，按 → 走下一张。
    未分组（group_key 为空）时只返回它自己，前端据此隐藏翻页按钮。
    """
    with get_conn() as conn:
        row = conn.execute("SELECT group_key FROM samples WHERE id = ?", (sample_id,)).fetchone()
        if not row:
            return {"ids": [], "index": -1}
        if not row["group_key"]:
            return {"ids": [sample_id], "index": 0}
        ids = [r["id"] for r in conn.execute(
            "SELECT id FROM samples WHERE group_key = ? ORDER BY id DESC", (row["group_key"],)
        ).fetchall()]
        return {"ids": ids, "index": ids.index(sample_id) if sample_id in ids else -1}


def update_sample(sample_id: int, category_id=..., is_favorite=..., note=..., shot_date=..., title=..., tags=None) -> bool:
    """局部更新。用 ... 作为"未传"的哨兵值。"""
    fields, params = [], []
    if category_id is not ...:
        fields.append("category_id = ?")
        params.append(category_id)
        fields.append("human_labeled = 1")   # 人工定过分类，永久退出纠错队列
    if is_favorite is not ...:
        fields.append("is_favorite = ?")
        params.append(1 if is_favorite else 0)
    if note is not ...:
        fields.append("note = ?")
        params.append(note or "")
    if shot_date is not ...:
        fields.append("shot_date = ?")
        params.append(shot_date or "")
    if title is not ...:
        fields.append("title = ?")
        params.append(title or "")

    with get_conn() as conn:
        if fields:
            fields.append("auto_labeled = 0")   # 人工改过就不再被自动规则覆盖
            conn.execute(f"UPDATE samples SET {', '.join(fields)} WHERE id = ?", params + [sample_id])
        if tags is not None:
            replace_sample_tags(conn, sample_id, tags)
        conn.commit()
    return True


def delete_sample(sample_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT rel_path, thumb_path FROM samples WHERE id = ?", (sample_id,)).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM samples WHERE id = ?", (sample_id,))
        conn.commit()
    storage.remove_file(row["rel_path"])
    if row["thumb_path"]:
        (storage.THUMBS_DIR / row["thumb_path"]).unlink(missing_ok=True)
    return True


# ============================================================
# 五、分类与标签
# ============================================================
def list_categories(with_count: bool = True) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM categories ORDER BY sort_order, id"
        ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["sample_count"] = (
                conn.execute("SELECT COUNT(*) FROM samples WHERE category_id = ?", (row["id"],)).fetchone()[0]
                if with_count else 0
            )
            result.append(data)
    return result


def create_category(name: str, keywords: str = "", color: str = "#6366f1",
                    description: str = "", parent_id: int | None = None) -> dict:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO categories(name, keywords, color, description, parent_id, sort_order)
               VALUES (?,?,?,?,?, (SELECT COALESCE(MAX(sort_order),0)+1 FROM categories))""",
            (name.strip(), keywords, color, description, parent_id),
        )
        conn.commit()
        return {"id": cur.lastrowid, "name": name}


def update_category(category_id: int, **fields) -> bool:
    allow = {"name", "keywords", "color", "description", "sort_order", "parent_id"}
    pairs = {k: v for k, v in fields.items() if k in allow and v is not None}
    if not pairs:
        return False
    with get_conn() as conn:
        conn.execute(
            f"UPDATE categories SET {', '.join(f'{k}=?' for k in pairs)} WHERE id = ?",
            list(pairs.values()) + [category_id],
        )
        conn.commit()
    return True


def delete_category(category_id: int) -> bool:
    """删除分类；其下样片回到"未分类"，不删图片。"""
    with get_conn() as conn:
        conn.execute("UPDATE samples SET category_id = NULL WHERE category_id = ?", (category_id,))
        conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        conn.commit()
    return True


def apply_category_standard() -> dict:
    """整体换成写真「画面状态」分类标准。

    删掉现有全部分类（样片随之回到"未分类"）→ 按预设重建 → 重跑一次归类 →
    再把抓到的话题标签同步成样片标签。图片文件本身不动。

    注意：分类标准一换，原有归类必然作废，所以这里会把 category_id 全部清空。
    """
    with get_conn() as conn:
        conn.execute("UPDATE samples SET category_id = NULL, auto_labeled = 0")
        conn.execute("DELETE FROM categories")
        conn.executemany(
            "INSERT INTO categories(name, keywords, color, sort_order) VALUES (?,?,?,?)",
            DEFAULT_CATEGORIES,
        )
        conn.commit()

    classified = auto_classify(only_unlabeled=True)
    tagged = sync_tags_from_hashtags()
    return {"categories": len(DEFAULT_CATEGORIES),
            "classified": classified["labeled"],
            "unclassified": classified["skipped"],
            "tagged": tagged["tagged"]}


def sync_tags_from_hashtags() -> dict:
    """把网页采集抓到的话题标签同步成样片的「标签」。

    只补还没打过标签的样片 —— 你手动加过的标签不会被覆盖。
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, source_hashtags FROM samples
                WHERE source_hashtags <> ''
                  AND id NOT IN (SELECT sample_id FROM sample_tags)"""
        ).fetchall()
        tagged = 0
        for row in rows:
            names = [t.strip() for t in (row["source_hashtags"] or "").replace("，", ",").split(",")
                     if t.strip()]
            if not names:
                continue
            replace_sample_tags(conn, row["id"], names)
            tagged += 1
        conn.commit()
    return {"tagged": tagged}


def list_tags() -> list:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT t.id, t.name, COUNT(st.sample_id) AS sample_count
               FROM tags t LEFT JOIN sample_tags st ON st.tag_id = t.id
               GROUP BY t.id ORDER BY sample_count DESC, t.name"""
        ).fetchall()
    return [dict(r) for r in rows]


def replace_sample_tags(conn, sample_id: int, tags: list) -> None:
    conn.execute("DELETE FROM sample_tags WHERE sample_id = ?", (sample_id,))
    for name in dict.fromkeys(t.strip() for t in tags if t and t.strip()):
        conn.execute("INSERT OR IGNORE INTO tags(name) VALUES (?)", (name,))
        tag_id = conn.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()["id"]
        conn.execute("INSERT OR IGNORE INTO sample_tags(sample_id, tag_id) VALUES (?,?)", (sample_id, tag_id))
    # 不再被任何样片使用的标签顺手清掉（比如刚人工去掉的「非人像」）
    conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM sample_tags)")


def set_sample_tags(sample_id: int, tags: list) -> bool:
    with get_conn() as conn:
        replace_sample_tags(conn, sample_id, tags)
        conn.commit()
    return True


# ============================================================
# 六、统计
# ============================================================
def stats() -> dict:
    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        by_category = [dict(r) for r in conn.execute(
            """SELECT COALESCE(c.name,'未分类') AS name, COALESCE(c.color,'#94a3b8') AS color,
                      COUNT(s.id) AS count
               FROM samples s LEFT JOIN categories c ON c.id = s.category_id
               GROUP BY s.category_id ORDER BY count DESC"""
        ).fetchall()]
        by_source = [dict(r) for r in conn.execute(
            "SELECT source_type AS name, COUNT(*) AS count FROM samples GROUP BY source_type"
        ).fetchall()]
        unlabeled = conn.execute(
            "SELECT COUNT(*) FROM samples WHERE category_id IS NULL"
        ).fetchone()[0]
    return {"total": total, "unlabeled": unlabeled,
            "by_category": by_category, "by_source": by_source,
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
