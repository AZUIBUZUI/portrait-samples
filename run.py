# -*- coding: utf-8 -*-
"""启动入口。

    python run.py             启动网页服务
    python run.py --login     登录一次小红书（扫码），保存会话供自动采集复用
    python run.py --watch     跑一遍关注博主，把新作品采进素材库（和页面按钮等价）
"""
import sys

from config import DEBUG, HOST, PORT


def main() -> int:
    args = set(sys.argv[1:])

    if "--login" in args:
        from core import browser
        return 0 if browser.login()["ok"] else 1

    if "--watch" in args:
        from core import watch
        from core.db import init_db
        init_db()
        # 默认无窗口跑；被风控时 browser 会自动退回有头。加 --headed 可强制开窗口看它抓什么
        result = watch.run_watchers(headless="--headed" not in args)
        print(result.get("message") or result.get("error") or "没有可跑的博主")
        return 0 if result.get("status") == "done" else 1

    from web import create_app
    app = create_app()
    print("=" * 52)
    print("  写真样片库 已启动")
    print(f"  浏览器打开: http://{HOST}:{PORT}")
    print("  首次采集前先执行: python run.py --login")
    print("=" * 52)
    # 关掉自动重载：采集要连着开几分钟浏览器，重载器一旦被别的文件变动触发重启，
    # 这次采集就被打断、页面请求直接断开（实测踩到：它连 site-packages 都在监视）。
    # 本地单用户工具不需要热重载。
    app.run(host=HOST, port=PORT, debug=DEBUG, use_reloader=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
