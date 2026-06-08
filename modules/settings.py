# -*- coding: utf-8 -*-
"""White-label settings — company profile, theme colors, logo, license, users."""
import os
import re
import time

from flask import Blueprint, render_template, request, redirect, url_for, flash, session

import config
import db

bp = Blueprint("settings", __name__, url_prefix="/settings")

COMPANY_FIELDS = ["name", "legal_name", "tagline", "license", "qualifier",
                  "address", "city", "state", "zip", "phone", "email", "website",
                  "color_masthead", "color_primary", "color_accent", "color_warn", "color_danger",
                  "default_county", "departments", "terms",
                  "photo_app_url", "tutorials"]


@bp.route("/department", methods=["POST"])
def set_department():
    session["department"] = request.form.get("department", "")
    return redirect(request.referrer or url_for("dashboard.home"))


@bp.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        data = {f: request.form.get(f, "").strip() for f in COMPANY_FIELDS}
        logo = request.files.get("logo")
        if logo and logo.filename:
            fn = "logo_%d_%s" % (int(time.time()), re.sub(r"[^A-Za-z0-9._-]+", "_", logo.filename))
            logo.save(os.path.join(config.LOGO_DIR, fn))
            data["logo_path"] = "branding/" + fn
        db.save_company(data)
        flash("Company settings saved — branding applied across the app and PDFs.", "ok")
        return redirect(url_for("settings.index"))
    return render_template("settings.html", c=db.get_company(), users=db.all_rows("users", order="id"))


@bp.route("/logo/clear", methods=["POST"])
def clear_logo():
    db.save_company({"logo_path": ""})
    flash("Logo cleared.", "ok")
    return redirect(url_for("settings.index"))


@bp.route("/users/new", methods=["POST"])
def user_new():
    from modules import auth
    uid = db.insert("users", {"name": request.form.get("name", "").strip(),
                              "email": request.form.get("email", "").strip(),
                              "role": request.form.get("role", "sales"), "active": 1})
    # Give the new user a usable login (provided password or the shared default).
    auth.set_password(uid, request.form.get("password", "").strip() or auth.DEFAULT_PASSWORD)
    flash("User added. Default password: %s (have them change it under My Account)." % (
        request.form.get("password", "").strip() or auth.DEFAULT_PASSWORD), "ok")
    return redirect(url_for("settings.index"))


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
def user_delete(user_id):
    db.delete("users", user_id)
    flash("User removed.", "ok")
    return redirect(url_for("settings.index"))
