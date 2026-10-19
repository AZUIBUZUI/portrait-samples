# -*- coding: utf-8 -*-
"""Flask 应用工厂：注册蓝图、初始化数据库、挂载媒体目录。"""
from flask import Flask, send_from_directory

from config import DEBUG, MAX_UPLOAD_MB, ORIGINALS_DIR, THUMBS_DIR
from core import storage
from core.db import init_db
from web.api import api
from web.pages import pages


def create_app() -> Flask:
    storage.ensure_dirs()
    init_db()

    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
    app.config["JSON_AS_ASCII"] = False

    app.register_blueprint(pages)
    app.register_blueprint(api, url_prefix="/api")

    @app.get("/media/original/<path:rel_path>")
    def media_original(rel_path: str):
        return send_from_directory(ORIGINALS_DIR, rel_path)

    @app.get("/media/thumb/<path:rel_path>")
    def media_thumb(rel_path: str):
        return send_from_directory(THUMBS_DIR, rel_path)

    @app.errorhandler(404)
    def not_found(_):
        return {"error": "接口不存在", "code": 404}, 404

    if DEBUG:
        print(f"[config] 数据目录: {storage.ORIGINALS_DIR.parent}")
    return app
