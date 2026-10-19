# -*- coding: utf-8 -*-
"""数据层：SQLite 连接与表结构。"""
import sqlite3
from contextlib import contextmanager

from config import DATA_DIR, DB_PATH

SCHEMA = """
-- 分类：支持二级目录（parent_id），keywords 为自动分类关键词
CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    parent_id   INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    keywords    TEXT    NOT NULL DEFAULT '',   -- 逗号分隔，命中即归入该分类
    color       TEXT    NOT NULL DEFAULT '#6366f1',
    description TEXT    NOT NULL DEFAULT '',
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 样片：file_hash 唯一，用于去重
CREATE TABLE IF NOT EXISTS samples (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file_hash     TEXT    NOT NULL UNIQUE,
    rel_path      TEXT    NOT NULL,              -- 原图相对 data/originals 的路径
    thumb_path    TEXT,                          -- 缩略图相对 data/thumbs 的路径
    filename      TEXT    NOT NULL DEFAULT '',
    ext           TEXT    NOT NULL DEFAULT '',
    width         INTEGER NOT NULL DEFAULT 0,
    height        INTEGER NOT NULL DEFAULT 0,
    file_size     INTEGER NOT NULL DEFAULT 0,
    source_type   TEXT    NOT NULL DEFAULT 'manual',  -- local / web / manual
    source_url    TEXT    NOT NULL DEFAULT '',   -- 原网页地址
    source_author TEXT    NOT NULL DEFAULT '',   -- 博主/摄影师
    title         TEXT    NOT NULL DEFAULT '',
    source_text   TEXT    NOT NULL DEFAULT '',   -- 原帖正文文案
    source_hashtags TEXT  NOT NULL DEFAULT '',   -- 作者自己打的话题标签，自动分类优先用它
    shot_date     TEXT    NOT NULL DEFAULT '',   -- 拍摄日期 YYYY-MM-DD
    group_key     TEXT    NOT NULL DEFAULT '',   -- 分组键：网页采集=原帖地址，本地=文件夹，上传=批次
    category_id   INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    is_favorite   INTEGER NOT NULL DEFAULT 0,
    auto_labeled  INTEGER NOT NULL DEFAULT 0,    -- 是否由规则/模型自动归类
    human_labeled INTEGER NOT NULL DEFAULT 0,    -- 是否人工标注/纠正过（永久排除出纠错队列）
    needs_review  INTEGER NOT NULL DEFAULT 0,    -- 是否是待人工纠正的样本（小批量标注用）
    note          TEXT    NOT NULL DEFAULT '',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS idx_samples_category ON samples(category_id);
CREATE INDEX IF NOT EXISTS idx_samples_source   ON samples(source_type);
CREATE INDEX IF NOT EXISTS idx_samples_date     ON samples(shot_date);

-- 标签：多对多，用于风格/场景/器材等自由标记
CREATE TABLE IF NOT EXISTS tags (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS sample_tags (
    sample_id INTEGER NOT NULL REFERENCES samples(id) ON DELETE CASCADE,
    tag_id    INTEGER NOT NULL REFERENCES tags(id)    ON DELETE CASCADE,
    PRIMARY KEY (sample_id, tag_id)
);

-- 采集任务：本地导入 / 上传 / 网页采集 都落一条记录，便于查看进度与失败原因
CREATE TABLE IF NOT EXISTS jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type  TEXT NOT NULL,                  -- local / manual / web
    target       TEXT NOT NULL DEFAULT '',       -- 文件夹路径或 URL
    status       TEXT NOT NULL DEFAULT 'pending',-- pending/running/done/failed
    total        INTEGER NOT NULL DEFAULT 0,
    succeeded    INTEGER NOT NULL DEFAULT 0,
    duplicated   INTEGER NOT NULL DEFAULT 0,
    failed       INTEGER NOT NULL DEFAULT 0,
    message      TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- 键值配置（预留）
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

-- 关注的博主：点一次采集就去抓 TA 的新作品
CREATE TABLE IF NOT EXISTS watchers (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    platform     TEXT    NOT NULL DEFAULT 'xiaohongshu',
    user_id      TEXT    NOT NULL DEFAULT '',
    url          TEXT    NOT NULL DEFAULT '',
    name         TEXT    NOT NULL DEFAULT '',
    enabled      INTEGER NOT NULL DEFAULT 1,
    last_run_at  TEXT    NOT NULL DEFAULT '',
    last_status  TEXT    NOT NULL DEFAULT '',   -- done / failed
    last_message TEXT    NOT NULL DEFAULT '',
    added_at     TEXT    NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_watchers_uniq ON watchers(platform, user_id);

-- 已经处理过的笔记：每次跑的时候只抓这里没记录过的，避免重复请求
-- （图片层还有 file_hash 内容去重兜底，两层都保证"重复跑不会重复添加"）
-- status=done/empty 的记录才算"处理过"；failed 的下次采集还会再试一次
CREATE TABLE IF NOT EXISTS seen_notes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    platform     TEXT    NOT NULL DEFAULT 'xiaohongshu',
    note_id      TEXT    NOT NULL,
    note_url     TEXT    NOT NULL DEFAULT '',
    title        TEXT    NOT NULL DEFAULT '',
    watcher_id   INTEGER NOT NULL DEFAULT 0,        -- 属于哪个关注博主
    status       TEXT    NOT NULL DEFAULT 'done',   -- done / empty / failed
    message      TEXT    NOT NULL DEFAULT '',
    images_added INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT   NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_seen_notes_uniq ON seen_notes(platform, note_id);
"""

# 写真样片的分类标准：按「画面状态」分，一张样片只归一类，彼此互斥。
# 风格 / 光线（日系、逆光、胶片、暗黑系…）不放在分类里 —— 它们存成样片标签，可以多选。
# 关键词只写描述**画面状态**的词；风格词写进分类关键词，会让整组图被误归到一类。
# 这里是唯一来源，core/library.py 的 apply_category_standard() 也从这里取。
DEFAULT_CATEGORIES = [
    ("安静", "安静,静谧,静坐,垂眸,留白,慵懒,松弛,情绪,故事感,忧郁,张力,孤独", "#0ea5e9", 0),
    ("元气", "元气,活力,明媚,开朗,俏皮",                     "#f59e0b", 2),
    ("酷拽", "酷拽,酷飒,酷感,高冷,街头,暗黑,机车,皮衣,墨镜,辣妹,朋克,赛博,拽姐,硬照,冷艳", "#111827", 6),
]


@contextmanager
def get_conn():
    """取一条连接，离开 with 块时自动提交并关闭。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)   # 采集/分类时会连续写，别用默认 5 秒就报 locked
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate(conn) -> None:
    """给已存在的库补新列。

    开发阶段不做版本化迁移，只做"缺什么补什么"；已有数据的库不能靠重建表升级。
    新库由 SCHEMA 直接建好，这里跑一遍也只是一次空操作。
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(samples)")}
    if "group_key" not in columns:
        conn.execute("ALTER TABLE samples ADD COLUMN group_key TEXT NOT NULL DEFAULT ''")
    if "source_text" not in columns:
        conn.execute("ALTER TABLE samples ADD COLUMN source_text TEXT NOT NULL DEFAULT ''")
    if "source_hashtags" not in columns:
        conn.execute("ALTER TABLE samples ADD COLUMN source_hashtags TEXT NOT NULL DEFAULT ''")
    if "needs_review" not in columns:
        conn.execute("ALTER TABLE samples ADD COLUMN needs_review INTEGER NOT NULL DEFAULT 0")
    if "human_labeled" not in columns:
        conn.execute("ALTER TABLE samples ADD COLUMN human_labeled INTEGER NOT NULL DEFAULT 0")
        # 老数据回填：人工改过分类的（auto_labeled=0 且有分类）都算已标注
        conn.execute(
            "UPDATE samples SET human_labeled = 1 "
            "WHERE auto_labeled = 0 AND category_id IS NOT NULL"
        )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_samples_group ON samples(group_key)")
    # 早先入库的网页采集样片没有分组，按原帖地址补上（可重复执行）
    conn.execute(
        "UPDATE samples SET group_key = source_url WHERE group_key = '' AND source_url <> ''"
    )
    seen_cols = {row["name"] for row in conn.execute("PRAGMA table_info(seen_notes)")}
    if seen_cols and "watcher_id" not in seen_cols:
        conn.execute("ALTER TABLE seen_notes ADD COLUMN watcher_id INTEGER NOT NULL DEFAULT 0")


def init_db() -> None:
    """建表 + 补列 + 初始化默认分类。应用启动时调用，可重复执行。"""
    with get_conn() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
            conn.executemany(
                "INSERT INTO categories(name, keywords, color, sort_order) VALUES (?,?,?,?)",
                DEFAULT_CATEGORIES,
            )
        conn.commit()
