# -*- coding: utf-8 -*-
"""小红书浏览器会话（playwright 驱动本机 Chrome）。

为什么非要用浏览器：博主主页的笔记列表是前端渲染的，纯 requests 拿到的是 SSR 壳，
笔记 id 被清空（"id":""），**拿不到每篇的跳转地址**；而且小红书对无登录态/无头访问会风控。

所以这里做三件事：
1. 登录：开一次有头窗口让你扫码，登录态存进持久化 profile 目录，之后一直复用；
2. 列笔记：读博主主页，从 DOM 里捞出「笔记地址 + 标题 + 封面」，翻页靠滚动；
3. 开笔记：**在这张主页里点开某篇笔记**（站内跳转才会带上访问用的 xsec_token；
   从站外直接打开 /explore/<id> 会缺 token，页面直接"不见了"），
   拿到弹窗里的整组图和 <head> 里的元信息，再把弹窗关掉。

取图的过滤/去重规则不在这里另写一套 —— 渲染出的 HTML 交给 core.collectors.web.parse_page，
全项目只有那一份 UI 元素剔除规则。

抓取默认先试 headless（无窗口、快），撞风控或登录墙时自动退回有头重试一次；
两种失败抛出不同的异常，方便上层给出可区分的提示。
"""
import re
import time
from contextlib import contextmanager
from urllib.parse import urlparse

from config import DATA_DIR
from core.collectors.web import parse_page

PROFILE_DIR = DATA_DIR / "browser_profile"   # 登录态与浏览器缓存的落盘位置
LOGIN_URL = "https://www.xiaohongshu.com/explore"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

NOTE_ID_RE = re.compile(r"/explore/([0-9a-fA-F]{16,})")
USER_ID_RE = re.compile(r"/user/profile/([0-9a-zA-Z]{16,})")
MODAL_SELECTOR = ".note-container"           # 笔记弹窗容器：图片只从这里取
CARD_TPL = 'a[href*="{note_id}"]:has(img)'   # 卡片锚点；没有 :has(img) 会点到隐藏的空锚点

# 主页里捞笔记：只要锚点带 /explore/<24位hex> 就算一篇
_EXTRACT_NOTES_JS = """
() => {
  const out = [], seen = new Set();
  for (const a of document.querySelectorAll('a[href*="/explore/"]')) {
    const href = a.getAttribute('href') || '';
    const m = href.match(/\\/explore\\/([0-9a-fA-F]{16,})/);
    if (!m) continue;
    if (seen.has(m[1])) continue;
    const card = a.closest('.note-item') || a.closest('section') || a.parentElement || a;
    const titleEl = card.querySelector('.title, .note-title');
    const img = card.querySelector('img');
    let cover = '';
    if (img) cover = img.getAttribute('src') || img.getAttribute('data-src') || '';
    seen.add(m[1]);
    out.push({
      id: m[1],
      url: new URL(href, location.origin).href,
      title: titleEl ? titleEl.textContent.trim()
                     : (img ? (img.getAttribute('alt') || '').trim() : ''),
      cover: cover,
    });
  }
  return out;
}
"""

_EXTRACT_NAME_JS = """
() => {
  const el = document.querySelector('.user-name, .user-nickname, .user-info .name');
  return el ? el.textContent.trim() : '';
}
"""


class BrowserError(RuntimeError):
    """浏览器通路失败。"""


class LoginRequired(BrowserError):
    """需要先人工登录一次（会话过期或从未登录）。"""


class RiskBlocked(BrowserError):
    """被平台风控拦截（IP 风险 / 验证码）。"""


# ============================================================
# 会话
# ============================================================
def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # 依赖缺失时给出可执行的修复建议
        raise BrowserError("未安装 playwright，请先执行：pip install playwright") from exc
    return sync_playwright().start()


@contextmanager
def session(headless: bool = True):
    """开一个持久化会话：复用 PROFILE_DIR 里的登录态，用完自动关闭。"""
    pw = _playwright()
    ctx = None
    try:
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel="chrome",                 # 用本机已装的 Chrome，不额外下载 Chromium
            headless=headless,
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            user_agent=UA,
            args=["--disable-blink-features=AutomationControlled"],
        )
        yield ctx
    finally:
        if ctx is not None:
            try:
                ctx.close()
            except Exception:
                pass
        try:
            pw.stop()
        except Exception:
            pass


def with_session(work, headless: bool = True, wait_login: bool = True):
    """跑一趟浏览器任务：work(page) 的返回值原样返回。

    headless 下被风控/撞登录墙时，自动改用有头窗口重试一次。
    有头模式下如果发现没登录，会把页面停在登录页等扫码，登录成功后**继续同一次任务**；
    等不到登录（超时/关窗口）才抛异常。
    """
    last = None
    for mode in ([True, False] if headless else [False]):
        try:
            with session(headless=mode) as ctx:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                try:
                    return work(page)
                except LoginRequired:
                    if not wait_login or mode:
                        raise            # 无头模式不在后台干等，换有头再处理
                    if _wait_login(page):
                        return work(page)
                    raise
        except (LoginRequired, RiskBlocked) as exc:
            last = exc
    raise last


LOGIN_WAIT_TIMEOUT = 600


def _wait_login(page, timeout: int = LOGIN_WAIT_TIMEOUT) -> bool:
    """把页面停在登录页等用户扫码；登录成功返回 True，超时/关窗口返回 False。"""
    print("未检测到小红书登录态：请在打开的浏览器窗口里扫码登录，登录后会自动继续采集…")
    try:
        if "/login" not in (page.url or ""):
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_logged_in(page):
            page.wait_for_timeout(2500)      # 给 cookie 落盘留时间
            if is_logged_in(page):
                print("登录成功，继续采集。")
                return True
        try:
            page.wait_for_timeout(1000)
        except Exception:
            return False                     # 窗口被关掉
    print("等待登录超时，本次采集已停止。")
    return False


def is_logged_in(page) -> bool:
    """判断当前是否已登录。

    不能只看 cookie：小红书给**未登录访客**也会下发一个 030037 开头的访客
    web_session，光看 cookie 存在会误判成"已登录"。看页面落在哪最准 ——
    未登录时任何站内页面都会被弹回 /login。
    """
    url = page.url or ""
    if not url or "/login" in url or "website-login/error" in url:
        return False
    return "xiaohongshu.com" in url


def login(timeout: int = 600) -> dict:
    """开有头窗口，让你手动扫码登录一次，登录态存进 profile 目录。

    这一步必须人工完成：小红书不接受代填，只能你来扫码。
    登录成功后关掉窗口即可，之后每次采集都读这份会话。
    """
    print("=" * 56)
    print("即将打开浏览器窗口，请在里面完成小红书登录（扫码/手机号均可）。")
    print("登录成功后**停留 2 秒以上**让程序保存会话，然后可以关掉窗口。")
    print(f"等待上限 {timeout} 秒。")
    print("=" * 56)
    with session(headless=False) as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if is_logged_in(page):
                page.wait_for_timeout(2500)   # 给 cookie 落盘留点时间
                if is_logged_in(page):
                    print("登录成功，会话已保存（data/browser_profile）。")
                    return {"ok": True, "message": "登录成功，会话已保存"}
            try:
                page.wait_for_timeout(1000)
            except Exception:
                break                          # 窗口被关掉
    print("未检测到登录态（超时或提前关闭窗口）。")
    return {"ok": False, "message": "未检测到登录态，请重试并确保登录后停留几秒"}


# ============================================================
# 抓取
# ============================================================
def _check_page(page) -> None:
    """落地后判断是否被拦：区分"要登录"和"被风控"，两种提示的处置方式不同。"""
    url = page.url or ""
    if "website-login/error" in url:
        raise RiskBlocked("被小红书风控拦截，先在本机浏览器正常打开一次小红书再试")
    if (urlparse(url).path or "").startswith("/login"):
        raise LoginRequired("需要登录小红书：请在弹出的浏览器窗口里扫码（或运行 python run.py --login）")


def _open(page, url: str, wait_ms: int = 2500) -> None:
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(wait_ms)
    _check_page(page)


def _collect_notes(page, limit: int) -> list:
    """滚动加载并去重，直到够 limit 篇或不再增长。"""
    notes, seen = [], set()
    for _ in range(12):
        for item in page.evaluate(_EXTRACT_NOTES_JS):
            if item["id"] in seen:
                continue
            seen.add(item["id"])
            notes.append(item)
        if len(notes) >= limit:
            break
        page.mouse.move(700, 500)
        page.mouse.wheel(0, 3000)
        page.wait_for_timeout(1500)
    return notes[:limit]


def read_profile(page, profile_url: str, limit: int = 30) -> dict:
    """读博主主页：返回 {name, notes:[{id, url, title, cover}]}。

    limit 是**本次翻页的上限**，不决定采集范围 —— 去重靠 seen_notes，
    所以重复跑只会补进新增的那几篇。
    """
    _open(page, profile_url, wait_ms=3500)
    try:
        page.wait_for_selector('a[href*="/explore/"]', timeout=15000)
    except Exception:
        pass
    try:
        name = (page.evaluate(_EXTRACT_NAME_JS) or "").strip()
    except Exception:
        name = ""
    return {"name": name, "notes": _collect_notes(page, limit)}


def open_note(page, note_id: str) -> dict:
    """在主页里点开某篇笔记，返回 {url, images, meta}，并关掉弹窗。

    元信息（标题/作者/正文/话题标签）来自 <head>：弹窗里没有这些 meta。
    图片只从弹窗容器里取：整页里还混着主页网格的封面图，一起抽会多出几十张。
    """
    page.click(CARD_TPL.format(note_id=note_id), timeout=20000)
    try:
        page.wait_for_url(re.compile(f"/explore/{re.escape(note_id)}"), timeout=15000)
    except Exception:
        pass                                  # URL 没变也先按"已点开"继续，靠下面判断
    page.wait_for_timeout(3000)

    note_url = page.url
    meta = parse_page(page.content(), note_url)[0]
    modal = page.query_selector(MODAL_SELECTOR)
    _, images = parse_page(modal.inner_html() if modal else page.content(), note_url)

    _close_note(page)
    return {"url": note_url, "images": images, "meta": meta, "opened": bool(modal)}


def _close_note(page) -> None:
    """关掉笔记弹窗，回到主页，好接着点下一篇。"""
    for _ in range(2):
        try:
            if not page.query_selector(MODAL_SELECTOR):
                return
            page.keyboard.press("Escape")
            page.wait_for_timeout(1200)
        except Exception:
            return


# ============================================================
# 地址解析
# ============================================================
def parse_user_id(url: str) -> str:
    """从博主主页地址里取 user_id；取不到返回空串。"""
    found = USER_ID_RE.search(url or "")
    return found.group(1) if found else ""


def parse_note_id(url: str) -> str:
    """从笔记地址里取 note_id；取不到返回空串。"""
    found = NOTE_ID_RE.search(url or "")
    return found.group(1) if found else ""
