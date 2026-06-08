# -*- coding: utf-8 -*-
"""SiteCam — embedded field-photo platform (CompanyCam-style).

SiteCam is the company's own app (photos.seabreezeroofing.com / sitecam-web on
Render). It allows iframe embedding (no X-Frame-Options / frame-ancestors), so we
host it full-screen inside the CRM. The URL is a white-label company setting.
"""
from flask import Blueprint, render_template

import db

bp = Blueprint("sitecam", __name__, url_prefix="/sitecam")

DEFAULT_URL = "https://sitecam-web.onrender.com/"


def url():
    return (db.get_company().get("sitecam_url") or DEFAULT_URL).strip()


@bp.route("/")
def index():
    return render_template("sitecam.html", sitecam_url=url())
