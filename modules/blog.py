# -*- coding: utf-8 -*-
"""MyRoofPortal blog — markdown posts on disk, no database.

Posts live in ``content/blog/<slug>.md`` with a small front-matter block::

    ---
    title: Roofing Payment Schedule: ...
    description: One-sentence meta description (<= 160 chars).
    date: 2026-09-28
    updated: 2026-09-28        # optional
    author: MyRoofPortal        # optional
    draft: false                # optional; true hides the post everywhere
    ---
    Markdown body...

The slug is the filename. Posts render inside the roofer front door's design
system (templates/portal_base.html) so the blog and the landing page share one
header, footer, token set and funnel beacon. ``all_posts()`` is what the sitemap
in modules/demos.py reads, so a new .md file is indexed with no other change.
"""
import datetime as _dt
import html as _html
import os
import re

from flask import Blueprint, abort, render_template, url_for
from markupsafe import Markup

try:  # python-markdown is in requirements.txt; the fallback keeps the page alive
    import markdown as _md
except Exception:  # pragma: no cover
    _md = None

bp = Blueprint("blog", __name__)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POSTS_DIR = os.environ.get("CRM_BLOG_DIR") or os.path.join(REPO, "content", "blog")
# Canonical public host for absolute URLs in canonical/og/ld+json (the sales site).
SITE = "https://" + (os.environ.get("CRM_CANONICAL_HOST") or "myroofportal.com").strip().lower()

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_cache = {"stamp": None, "posts": []}


def _parse_date(v):
    v = (v or "").strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%m/%d/%Y"):
        try:
            return _dt.datetime.strptime(v, fmt).date()
        except ValueError:
            continue
    return None


def _split_front_matter(text):
    text = text.lstrip("﻿")
    if not text.startswith("---"):
        return {}, text
    parts = text.split("\n---", 1)
    if len(parts) < 2:
        return {}, text
    head = parts[0][3:]
    body = parts[1].lstrip("-").lstrip("\n")
    meta = {}
    for line in head.splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        k, v = line.split(":", 1)
        meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    return meta, body


def _render_markdown(body):
    if _md is not None:
        return _label_table_cells(
            _md.markdown(body, extensions=["extra", "sane_lists", "toc"],
                         extension_configs={"toc": {"permalink": False}},
                         output_format="html5"))
    # Minimal fallback: paragraphs + headings + lists, escaped. Good enough to
    # keep the page up if the library is ever missing on a host.
    out, para = [], []

    def flush():
        if para:
            out.append("<p>%s</p>" % " ".join(para))
            para[:] = []
    in_list = False
    for raw in body.splitlines():
        line = _html.escape(raw.rstrip())
        if not line.strip():
            flush()
            if in_list:
                out.append("</ul>")
                in_list = False
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            flush()
            n = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (n, m.group(2), n))
            continue
        if line.startswith(("- ", "* ")):
            flush()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append("<li>%s</li>" % line[2:])
            continue
        para.append(line)
    flush()
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


_TABLE_RE = re.compile(r"<table>.*?</table>", re.S)
_TH_RE = re.compile(r"<th[^>]*>(.*?)</th>", re.S)
_TD_RE = re.compile(r"<td([^>]*)>", re.S)


def _label_table_cells(html_out):
    """Stamp every <td> with data-th="<its column header>" so the phone layout can
    stack each row into a labelled card (markdown tables carry no such attribute)."""
    def one(m):
        tbl = m.group(0)
        heads = [re.sub(r"<[^>]+>", "", h).strip() for h in _TH_RE.findall(tbl)]
        if not heads:
            return tbl
        head_end = tbl.find("</thead>")
        head, body = (tbl[:head_end], tbl[head_end:]) if head_end > 0 else ("", tbl)
        idx = {"i": 0}

        def td(mm):
            i = idx["i"] % len(heads)
            idx["i"] += 1
            return '<td%s data-th="%s">' % (mm.group(1), _html.escape(heads[i], quote=True))
        # reset the column counter at every row
        rows = body.split("<tr>")
        out = []
        for r in rows:
            idx["i"] = 0
            out.append(_TD_RE.sub(td, r))
        return head + "<tr>".join(out)
    return _TABLE_RE.sub(one, html_out)


def _read_time(body):
    words = len(re.findall(r"\w+", body))
    return max(1, int(round(words / 220.0)))


def _load_post(path):
    slug = os.path.splitext(os.path.basename(path))[0]
    if not _SLUG_RE.match(slug):
        return None
    with open(path, encoding="utf-8") as fh:
        meta, body = _split_front_matter(fh.read())
    if (meta.get("draft") or "").lower() in ("1", "true", "yes"):
        return None
    title = meta.get("title") or slug.replace("-", " ").title()
    date = _parse_date(meta.get("date")) or _dt.date.fromtimestamp(os.path.getmtime(path))
    updated = _parse_date(meta.get("updated")) or date
    return {
        "slug": slug,
        "title": title,
        "description": (meta.get("description") or "")[:300],
        "date": date,
        "date_h": date.strftime("%B %d, %Y").replace(" 0", " "),
        "updated": updated,
        "author": meta.get("author") or "MyRoofPortal",
        "body_md": body,
        "read_min": _read_time(body),
        "path": "/blog/%s" % slug,
        "url": "%s/blog/%s" % (SITE, slug),
    }


def _dir_stamp():
    try:
        names = sorted(n for n in os.listdir(POSTS_DIR) if n.endswith(".md"))
    except OSError:
        return ()
    return tuple((n, os.path.getmtime(os.path.join(POSTS_DIR, n))) for n in names)


def all_posts():
    """Every published post, newest first. Re-reads only when a file changes."""
    stamp = _dir_stamp()
    if stamp != _cache["stamp"]:
        posts = []
        for name, _mt in stamp:
            try:
                p = _load_post(os.path.join(POSTS_DIR, name))
            except Exception:
                p = None
            if p:
                posts.append(p)
        posts.sort(key=lambda p: (p["date"], p["slug"]), reverse=True)
        _cache["stamp"], _cache["posts"] = stamp, posts
    return list(_cache["posts"])


def get_post(slug):
    for p in all_posts():
        if p["slug"] == slug:
            return p
    return None


def _log(kind, path):
    # Same funnel table as the landing page; imported lazily to avoid a cycle.
    try:
        from modules.demos import _log_event
        _log_event(kind, path)
    except Exception:
        pass


@bp.route("/blog", endpoint="index")
def index():
    _log("page_view_server", "/blog")
    posts = all_posts()
    return render_template(
        "blog_index.html", posts=posts, site=SITE,
        canonical="%s/blog" % SITE, beacon_kind="blog_view",
        demo_url=url_for("demo.portal", slug=_demo_slug()),
        acquire_url=url_for("demo.acquire"))


@bp.route("/blog/<slug>", endpoint="post")
def post(slug):
    if not _SLUG_RE.match(slug or ""):
        abort(404)
    p = get_post(slug)
    if not p:
        abort(404)
    _log("page_view_server", p["path"])
    others = [o for o in all_posts() if o["slug"] != slug][:3]
    return render_template(
        "blog_post.html", post=p, body=Markup(_render_markdown(p["body_md"])),
        others=others, site=SITE, canonical=p["url"], beacon_kind="blog_view",
        demo_url=url_for("demo.portal", slug=_demo_slug()),
        acquire_url=url_for("demo.acquire"))


def _demo_slug():
    try:
        from modules.demos import DEMO_SLUG
        return DEMO_SLUG
    except Exception:
        return "roof-portal"
