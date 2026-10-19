# -*- coding: utf-8 -*-
"""页面路由。三个页面：图库 / 采集 / 分类管理。"""
from flask import Blueprint, render_template

pages = Blueprint("pages", __name__)


@pages.get("/")
def gallery_page():
    return render_template("gallery.html", active="gallery")


@pages.get("/collect")
def collect_page():
    return render_template("collect.html", active="collect")


@pages.get("/categories")
def categories_page():
    return render_template("categories.html", active="categories")


@pages.get("/watch")
def watch_page():
    return render_template("watch.html", active="watch")


@pages.get("/label")
def label_page():
    return render_template("label.html", active="label")
