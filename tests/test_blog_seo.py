# -*- coding: utf-8 -*-
"""Public sales-site SEO + blog regression guard (myroofportal.com).

Locks down the 2026-09-28 fixes and the markdown blog:

* /acquire canonicals to itself (it used to point at "/", which de-indexed the
  acquisition page once the roofer front door moved to the root).
* /portal-sales/licensing is noindex (it duplicated the homepage pitch).
* sitemap.xml lists / , /acquire, the demo, /blog and every published post, and
  no longer lists the noindexed licensing page.
* The demo portal carries a description + canonical (it is in the sitemap).
* The roofer front door carries Organization + SoftwareApplication ld+json and
  links /blog from header nav + footer.
* /blog and /blog/<slug> render inside the shared chrome with title, description,
  canonical, og tags, Article ld+json, a published date and the funnel beacon;
  unknown / malformed slugs 404.

Runs the real app on a throwaway SQLite DB in a subprocess (same isolation as
test_fresh_db_smoke.py) so nothing here touches the dev database.
"""
import json
import os
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_CHECK = r'''
import json, os, re, sys
sys.path.insert(0, REPO_PLACEHOLDER)
os.chdir(REPO_PLACEHOLDER)
import app as appmod
from modules.blog import all_posts
c = appmod.app.test_client()
H = {"Host": "myroofportal.com"}
out = {}

def get(p):
    r = c.get(p, headers=H)
    return r.status_code, r.data.decode("utf-8", "replace")

s, acquire = get("/acquire");                 out["acquire"] = (s, acquire)
s, lic = get("/portal-sales/licensing");      out["licensing"] = (s, lic)
s, sm = get("/sitemap.xml");                  out["sitemap"] = (s, sm)
s, demo = get("/demo/roof-portal");           out["demo"] = (s, demo)
s, root = get("/");                           out["root"] = (s, root)
s, idx = get("/blog");                        out["blog"] = (s, idx)
posts = all_posts()
out["posts"] = [{"slug": p["slug"], "path": p["path"], "date": p["date"].isoformat()} for p in posts]
if posts:
    s, post = get(posts[0]["path"]);          out["post"] = (s, post)
out["nope"] = get("/blog/no-such-post")[0]
out["bad"] = get("/blog/Bad_Slug")[0]
out["trav"] = get("/blog/..%2fapp")[0]
print("@@JSON@@" + json.dumps(out))
'''


def _run():
    tmp = tempfile.mkdtemp(prefix="crm_blogtest_")
    env = dict(os.environ)
    env.update({
        "DATABASE_URL": "", "CRM_NOBROWSER": "1",
        "CRM_DB_PATH": os.path.join(tmp, "crm.db"),
        "CRM_DATA_DIR": tmp, "CRM_UPLOAD_DIR": os.path.join(tmp, "uploads"),
        "PYTHONIOENCODING": "utf-8",
    })
    code = _CHECK.replace("REPO_PLACEHOLDER", repr(REPO))
    r = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                       capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert r.returncode == 0, "subprocess failed:\n%s\n%s" % (r.stdout[-2000:], r.stderr[-4000:])
    blob = r.stdout.split("@@JSON@@", 1)[1]
    return json.loads(blob)


_OUT = None


def _out():
    global _OUT
    if _OUT is None:
        _OUT = _run()
    return _OUT


def test_acquire_canonicals_to_itself():
    s, html = _out()["acquire"]
    assert s == 200
    assert '<link rel="canonical" href="https://myroofportal.com/acquire">' in html
    assert '<meta property="og:url" content="https://myroofportal.com/acquire">' in html
    assert '<link rel="canonical" href="https://myroofportal.com/">' not in html


def test_licensing_page_is_noindex():
    s, html = _out()["licensing"]
    assert s == 200
    assert 'name="robots" content="noindex,follow"' in html


def test_sitemap_lists_public_pages_and_posts_not_licensing():
    s, xml = _out()["sitemap"]
    assert s == 200
    for loc in ("https://myroofportal.com/</loc>", "https://myroofportal.com/acquire</loc>",
                "https://myroofportal.com/demo/roof-portal</loc>", "https://myroofportal.com/blog</loc>"):
        assert loc in xml, loc
    assert "/portal-sales/licensing" not in xml
    posts = _out()["posts"]
    assert len(posts) >= 3, "expected the three launch posts on disk"
    for p in posts:
        assert "https://myroofportal.com%s</loc>" % p["path"] in xml, p["path"]
        assert "<lastmod>%s</lastmod>" % p["date"] in xml


def test_demo_portal_has_description_and_canonical():
    s, html = _out()["demo"]
    assert s == 200
    assert '<meta name="description" content="' in html
    assert '<link rel="canonical" href="https://myroofportal.com/demo/roof-portal" />' in html
    assert 'name="robots" content="index,follow"' in html


def test_root_has_org_and_software_ldjson_and_blog_links():
    s, html = _out()["root"]
    assert s == 200
    blocks = _ldjson(html)
    graph = [b for b in blocks if "@graph" in b]
    assert graph, "no @graph ld+json on /"
    types = {n.get("@type") for n in graph[0]["@graph"]}
    assert {"Organization", "SoftwareApplication"} <= types
    app = [n for n in graph[0]["@graph"] if n["@type"] == "SoftwareApplication"][0]
    assert {o["price"] for o in app["offers"]} == {"1997", "4997"}
    # header nav + footer both link the blog
    assert html.count('href="/blog"') >= 2
    assert 'class="flinks"' in html
    # existing chrome still there
    assert 'class="cta-gold"' in html and "<footer>" in html


def test_blog_index_renders_in_shared_chrome():
    s, html = _out()["blog"]
    assert s == 200
    assert '<link rel="canonical" href="https://myroofportal.com/blog">' in html
    assert "<title>" in html and 'name="description"' in html
    assert 'property="og:title"' in html
    assert 'hit("blog_view")' in html and "/portal-sales/ev" in html
    assert 'class="brand">myroofportal' in html and "<footer>" in html
    for p in _out()["posts"]:
        assert 'href="%s"' % p["path"] in html, p["path"]


def test_blog_post_has_full_seo_head_and_article_ldjson():
    s, html = _out()["post"]
    assert s == 200
    p = _out()["posts"][0]
    canon = "https://myroofportal.com" + p["path"]
    assert '<link rel="canonical" href="%s">' % canon in html
    assert '<meta property="og:type" content="article">' in html
    assert '<meta property="og:url" content="%s">' % canon in html
    assert 'name="description" content="' in html
    assert '<time datetime="%s">' % p["date"] in html
    assert 'hit("blog_view")' in html
    art = [b for b in _ldjson(html) if b.get("@type") == "Article"]
    assert art, "no Article ld+json"
    assert art[0]["datePublished"] == p["date"]
    assert art[0]["mainEntityOfPage"]["@id"] == canon
    assert art[0]["headline"]
    # markdown actually rendered (headings, not raw '##')
    assert "<h2" in html and "\n## " not in html.split("<article", 1)[1]


def test_blog_unknown_and_malformed_slugs_404():
    o = _out()
    assert o["nope"] == 404
    assert o["bad"] == 404
    # "%2f" is normalised away by Werkzeug before routing; it must never resolve
    # to a post (the auth guard answers for whatever it becomes).
    assert o["trav"] in (302, 404)


def _ldjson(html):
    import re
    out = []
    for m in re.finditer(r'<script type="application/ld\+json">\s*(.*?)\s*</script>', html, re.S):
        out.append(json.loads(m.group(1)))
    return out
