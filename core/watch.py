# -*- coding: utf-8 -*-
"""关注博主：把 TA 的新作品补进素材库（手动触发，重复跑不重复添加）。

触发方式：网页「关注博主」页点「立即采集」，或命令行 `python run.py --watch`。
没有定时任务在背后跑 —— 什么时候补由你决定。

为什么重复跑不会重复添加 —— 两道防线：
1. seen_notes：这篇笔记处理过（done / empty）就不再请求第二次；
2. samples.file_hash 唯一索引：即使同一张图从别的地址再进来，也只会命中已有记录。
失败的笔记**不写 done**，下次采集会自动重试，所以偶发的网络/风控问题不会让作品永久漏采。

单篇笔记一个分组（group_key = 笔记地址），方便在图库里按原文成组浏览。
"""
from datetime import datetime

from config import WEB_MIN_SIDE
from core import browser, library
from core.collectors.base import is_gif_bytes, looks_like_meme
from core.collectors.web import download_image
from core.db import get_conn
from core.storage import guess_filename, image_size

PLATFORM = "xiaohongshu"
SKIP_STATUSES = ("done", "empty", "skipped")   # 这三种算"处理过了"，failed 要重试


# ============================================================
# 一、关注列表维护
# ============================================================
def add_watcher(url: str, name: str = "") -> dict:
    """加一个要自动跟的博主。同一个博主重复添加只会更新名字，不会建两条。"""
    url = (url or "").strip()
    user_id = browser.parse_user_id(url)
    if not user_id:
        return {"ok": False, "error": "没能从链接里认出博主 id，请填主页完整地址"
                                      "（形如 https://www.xiaohongshu.com/user/profile/xxxx）"}
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO watchers(platform, user_id, url, name)
               VALUES (?,?,?,?)
               ON CONFLICT(platform, user_id) DO UPDATE SET url = excluded.url,
                   name = CASE WHEN excluded.name <> '' THEN excluded.name ELSE watchers.name END""",
            (PLATFORM, user_id, url, name.strip()),
        )
        row = conn.execute(
            "SELECT * FROM watchers WHERE platform = ? AND user_id = ?", (PLATFORM, user_id)
        ).fetchone()
        conn.commit()
    return {"ok": True, "watcher": dict(row)}


def list_watchers() -> list:
    """关注列表 + 每个博主累计采了多少篇笔记、多少张图。"""
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM watchers ORDER BY id").fetchall()
        result = []
        for row in rows:
            data = dict(row)
            stat = conn.execute(
                """SELECT COUNT(*) AS notes, COALESCE(SUM(images_added), 0) AS images
                   FROM seen_notes WHERE watcher_id = ? AND status IN ('done', 'empty')""",
                (row["id"],),
            ).fetchone()
            data["note_count"] = stat["notes"]
            data["image_count"] = stat["images"]
            result.append(data)
    return result


def update_watcher(watcher_id: int, name=None, url=None, enabled=None) -> dict:
    fields, params = [], []
    if name is not None:
        fields.append("name = ?")
        params.append(name.strip())
    if url is not None:
        user_id = browser.parse_user_id(url)
        if not user_id:
            return {"ok": False, "error": "链接里认不出博主 id"}
        fields.append("url = ?")
        params.append(url.strip())
        fields.append("user_id = ?")
        params.append(user_id)
    if enabled is not None:
        fields.append("enabled = ?")
        params.append(1 if enabled else 0)
    if not fields:
        return {"ok": True}
    with get_conn() as conn:
        conn.execute(f"UPDATE watchers SET {', '.join(fields)} WHERE id = ?", params + [watcher_id])
        conn.commit()
    return {"ok": True}


def delete_watcher(watcher_id: int) -> bool:
    """删除关注。已采进来的样片与 seen_notes 记录都保留 —— 删关注不等于删素材。"""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM watchers WHERE id = ?", (watcher_id,))
        conn.commit()
        return cur.rowcount > 0


def get_watcher(watcher_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM watchers WHERE id = ?", (watcher_id,)).fetchone()
    return dict(row) if row else None


# ============================================================
# 二、跑采集
# ============================================================
def run_watchers(watcher_ids: list | None = None, limit: int = 30,
                 headless: bool = True) -> dict:
    """跑一遍关注列表。

    watcher_ids 为空时跑所有**已启用**的博主；给定 id 时按指定跑（"立即采集"用，
    即使这个博主处于停用状态也允许手动跑一次）。
    """
    targets = [w for w in list_watchers()
               if (w["id"] in watcher_ids if watcher_ids else w["enabled"])]
    if not targets:
        return {"ok": False, "error": "没有可跑的博主（关注列表为空，或被停用了）"}

    job_id = library.start_job("watch", f"关注博主 {len(targets)} 位")
    total_notes = new_notes = images_added = duplicated = skipped = filtered = 0
    details, failed_notes, failed_watchers = [], [], 0
    for watcher in targets:
        result = _run_one(watcher, limit, headless)
        new_notes += result["new_notes"]
        images_added += result["images_added"]
        duplicated += result["duplicated"]
        skipped += result["skipped"]
        filtered += result["filtered"]
        total_notes += result["scanned"]
        if result["error"]:
            failed_watchers += 1
            details.append(f"{result['name']}: 抓取失败（{result['error']}）")
        else:
            line = f"{result['name']}: 新 {result['new_notes']} 篇 / 图 +{result['images_added']}"
            extras = []
            if result["skipped"]:
                extras.append(f"跳过 {result['skipped']} 篇非人像")
            if result["filtered"]:
                extras.append(f"过滤 {result['filtered']} 张（表情包/低清）")
            if extras:
                line += f"（{'，'.join(extras)}）"
            details.append(line)
        failed_notes.extend(result["errors"])

    message = "; ".join(details)
    if failed_notes:
        message += f"; {len(failed_notes)} 篇没抓全，下次自动重试"
    status = "done" if (images_added or new_notes or skipped) else ("failed" if (failed_notes or failed_watchers) else "done")
    library.finish_job(job_id, status, total_notes, images_added, duplicated,
                       len(failed_notes) + failed_watchers, message)

    return {
        "job_id": job_id, "status": status, "watchers": len(targets),
        "scanned": total_notes, "new_notes": new_notes,
        "images_added": images_added, "duplicated": duplicated,
        "skipped": skipped, "filtered": filtered,
        "failed": len(failed_notes) + failed_watchers, "message": message,
    }


def _run_one(watcher: dict, limit: int, headless: bool) -> dict:
    """跑单个博主：列笔记 → 跳过处理过的 → 点开新笔记，整组图入库。

    error 表示整位博主没抓到（多为登录态失效/风控）；errors 是单篇笔记的失败。
    两者都会让这条记录显示为失败，也都不写 seen_notes，下次自动重试。
    """
    summary = {"name": watcher["name"] or watcher["user_id"], "scanned": 0, "new_notes": 0,
               "images_added": 0, "duplicated": 0, "skipped": 0, "filtered": 0,
               "errors": [], "error": ""}

    def work(page):
        profile = browser.read_profile(page, watcher["url"], limit)
        # 名字没填过就用主页上的昵称补上，之后显示更直观
        if not watcher["name"] and profile["name"]:
            update_watcher(watcher["id"], name=profile["name"])
            summary["name"] = profile["name"]

        notes = profile["notes"]
        summary["scanned"] = len(notes)
        done = _processed_note_ids(PLATFORM)
        for note in [n for n in notes if n["id"] not in done]:
            outcome = _ingest_note(page, watcher, summary["name"], note)
            if outcome["status"] == "failed":
                summary["errors"].append(f"{note['title'][:20] or note['id']}: {outcome['reason']}")
                continue
            if outcome["status"] == "skipped":
                summary["skipped"] += 1
                continue
            if outcome["status"] == "new":
                summary["new_notes"] += 1
            summary["images_added"] += outcome["added"]
            summary["duplicated"] += outcome["duplicated"]
            summary["filtered"] += outcome.get("filtered", 0)
        return summary

    try:
        summary = browser.with_session(work, headless=headless)
    except Exception as exc:
        summary["name"] = watcher["name"] or "未命名博主"
        summary["error"] = str(exc)

    _finish_watcher(
        watcher["id"], "failed" if (summary["error"] or summary["errors"]) else "done",
        summary["error"] or
        f"新增 {summary['new_notes']} 篇 / {summary['images_added']} 张，"
        f"重复 {summary['duplicated']} 张"
        + (f"，跳过 {summary['skipped']} 篇非人像" if summary["skipped"] else "")
        + (f"，过滤 {summary['filtered']} 张（表情包/低清）" if summary["filtered"] else "")
        + (f"，失败 {len(summary['errors'])} 篇" if summary["errors"] else ""),
    )
    return summary


def _ingest_note(page, watcher: dict, author_name: str, note: dict) -> dict:
    """点开一篇笔记，先按原帖文字判断是不是人像写真，再决定要不要把整组图抓下来。"""
    note_id = note["id"]
    try:
        detail = browser.open_note(page, note_id)
    except Exception as exc:
        return {"status": "failed", "reason": str(exc), "added": 0, "duplicated": 0}

    note_url = detail["url"] or note["url"]
    images, meta = detail["images"], detail["meta"]
    title = meta["title"] or note["title"]

    # 导入闸：明确不是人像写真的整篇跳过（省下这一组的下载）；拿不准的先收下并标记
    verdict, why = library.judge_portrait(title, meta["hashtags"])
    if verdict == "skip":
        _mark_note(watcher["id"], note_id, note_url, title, "skipped", why, 0)
        return {"status": "skipped", "reason": why, "added": 0, "duplicated": 0}

    if not images:
        _mark_note(watcher["id"], note_id, note_url, title, "empty", "页面里没找到图片", 0)
        return {"status": "empty", "reason": "页面里没找到图片", "added": 0, "duplicated": 0}

    name = meta["author"] or author_name
    pending = verdict == "unsure"
    added = duplicated = filtered = 0
    for url in images:
        filename = guess_filename(url)
        try:
            data = download_image(url)
        except Exception:
            continue                       # 单张失败不影响这一篇的其他图
        # 组图里夹带的表情包 / 梗图 / gif，以及短边低于阈值的糊图，都不进库
        if looks_like_meme(filename) or is_gif_bytes(data):
            filtered += 1
            continue
        width, height = image_size(data)
        if width and height and min(width, height) < WEB_MIN_SIDE:
            filtered += 1
            continue
        result = library.ingest_bytes(
            data, filename, source_type="web", source_url=note_url,
            author=name, title=title, source_text=meta["text"],
            source_hashtags=meta["hashtags"], group_key=note_url,
            note=library.PENDING_NOTE if pending else "",
            shot_date=meta.get("published_at", ""),
        )
        if result["status"] == "added":
            added += 1
            tags = library.tag_names(meta["hashtags"]) + ([library.PENDING_TAG] if pending else [])
            if tags:
                library.set_sample_tags(result["sample_id"], tags)
        elif result["status"] == "duplicated":
            duplicated += 1

    if added:
        status, outcome = "done", "new"
    elif duplicated:
        status, outcome = "done", "old"    # 图早就在库里（比如你先前手工采过），这篇不算漏采
    else:
        status, outcome = "failed", "failed"

    reason = "" if status == "done" else "这一组图一张都没下来"
    _mark_note(watcher["id"], note_id, note_url, title, status,
               f"新增 {added} / 重复 {duplicated}"
               + (f" / 过滤 {filtered} 张（表情包/低清）" if filtered else "")
               + (f"；{reason}" if reason else ""), added)
    return {"status": outcome, "reason": reason, "added": added,
            "duplicated": duplicated, "filtered": filtered}


# ============================================================
# 三、seen_notes 读写
# ============================================================
def _processed_note_ids(platform: str) -> set:
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT note_id FROM seen_notes WHERE platform = ? "
            f"AND status IN {SKIP_STATUSES}", (platform,)
        ).fetchall()
    return {r["note_id"] for r in rows}


def _mark_note(watcher_id: int, note_id: str, note_url: str, title: str,
               status: str, message: str, images_added: int) -> None:
    """登记一篇笔记的处理结果（同一篇重复登记只会更新，不会多出记录）。"""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO seen_notes(platform, note_id, note_url, title, watcher_id,
                                      status, message, images_added)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(platform, note_id) DO UPDATE SET
                   status = excluded.status, message = excluded.message,
                   images_added = MAX(seen_notes.images_added, excluded.images_added),
                   watcher_id = excluded.watcher_id, title = excluded.title""",
            (PLATFORM, note_id, note_url, title[:120], watcher_id, status, message[:200], images_added),
        )
        conn.commit()


def _finish_watcher(watcher_id: int, status: str, message: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE watchers SET last_run_at = ?, last_status = ?, last_message = ? WHERE id = ?",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), status, message[:300], watcher_id),
        )
        conn.commit()


def list_seen_notes(limit: int = 30) -> list:
    """最近处理过的笔记，给界面看"每天到底采了什么"。"""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT n.*, COALESCE(w.name, '') AS watcher_name
               FROM seen_notes n LEFT JOIN watchers w ON w.id = n.watcher_id
               ORDER BY n.id DESC LIMIT ?""", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]
