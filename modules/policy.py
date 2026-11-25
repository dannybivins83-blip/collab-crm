# -*- coding: utf-8 -*-
"""Public privacy policy and terms.

These are not boilerplate. The homeowner pages make three specific promises --
"we don't sell your details, we don't bid on your roof, and nobody calls you"
-- and a policy is only worth publishing if it describes what the code
actually does. Every statement on these pages was checked against the source
before it was written:

  * modules/compare.py is the only module that reads ho_projects, ho_quotes or
    ho_uploads. Nothing copies a homeowner into a lead, contact or sales table,
    which is what makes the "we don't bid on your roof" promise structural
    rather than a policy anyone has to remember to keep.
  * read_proposal() sends the uploaded file to the Anthropic API. That is a
    real disclosure and it is the first thing the policy says, because a
    homeowner handing over a document deserves to know it leaves the server.
  * _log()/_log_event() record kind, path, referrer, user-agent and host. No IP
    address, no email, and the workspace routes log a masked path rather than
    the token.
  * portal_base.html loads fonts from Google, so a visitor's browser contacts
    Google and Google sees their IP. Disclosed rather than ignored.
  * There is no payment path anywhere in the homeowner flow, so "free" is
    stated plainly and without conditions.

If any of that changes, this file changes with it.
"""
from flask import Blueprint, render_template

bp = Blueprint("policy", __name__)


# The shared footer links the live demo and the acquisition page from variables
# each view had to remember to pass. Four views did; these two did not, and the
# links rendered as href="" -- the exact empty-footer defect a previous audit
# had already caught and seen fixed. A view should not be able to reintroduce
# it by forgetting, so the footer's own URLs are supplied app-wide and any view
# that passes its own still wins.
@bp.app_context_processor
def _footer_urls():
    from flask import url_for
    try:
        from modules.demos import DEMO_SLUG
        demo = url_for("demo.portal", slug=DEMO_SLUG)
    except Exception:
        demo = "/demo/roof-portal"
    try:
        acquire = url_for("demo.acquire")
    except Exception:
        acquire = "/acquire"
    return {"demo_url": demo, "acquire_url": acquire}

# The one fact that is a business decision rather than an observable property
# of the code. Kept here so it is changed in one place.
CONTACT_EMAIL = "info@collaborativeconceptsfl.com"

# How long a workspace survives without being opened. A comparison is useful
# while a homeowner is choosing and dead weight afterwards, and keeping other
# people's roofing proposals indefinitely is a liability, not a feature.
RETENTION_MONTHS = 18


@bp.route("/privacy", endpoint="privacy")
def privacy():
    return render_template("policy_privacy.html", beacon_kind="policy_view",
                           contact_email=CONTACT_EMAIL,
                           retention_months=RETENTION_MONTHS)


@bp.route("/terms", endpoint="terms")
def terms():
    return render_template("policy_terms.html", beacon_kind="policy_view",
                           contact_email=CONTACT_EMAIL)
