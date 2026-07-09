# -*- coding: utf-8 -*-
"""Static integrity guard for EVERY Jinja template in ``templates/``.

Motivation — the LIVE-500 class this locks down:

    templates/contacts.html was once pasted with smart/curly quotes. Its first
    line, ``{% extends "base.html" %}``, used U+201C/U+201D instead of ASCII
    double quotes, so Jinja could not tokenize the template and EVERY load of
    ``/contacts/`` returned HTTP 500 (normal usage, not an edge case). The same
    paste also swapped the HTML attribute delimiters (``class=...``, ``href=...``,
    ``name="q"``) for curly quotes, so even a template that *did* compile would
    render structurally broken HTML — dead nav links and a search box that
    submitted the wrong field name.

This module walks the real ``templates/`` directory and asserts three things,
none of which needs a live request / route context:

1. Every ``*.html`` template COMPILES through a Jinja environment. This catches
   ``TemplateSyntaxError`` — the exact failure mode of the curly-quote
   ``{% extends %}`` that 500'd Contacts, plus any unclosed block, bad filter
   syntax, or stray delimiter introduced by a future edit.

2. No smart/curly quote (or BOM / zero-width space) is used as a *delimiter* —
   i.e. inside a ``{{ ... }}`` / ``{% ... %}`` block, or immediately after ``=``
   as an HTML attribute delimiter. Curly quotes in visible prose are legitimate
   and are left alone; only code-position uses are rejected. Compilation alone
   does NOT catch the attribute-delimiter half of the Contacts bug (it renders
   broken HTML without raising), so this check guards that half explicitly.

3. Every static ``{% extends %}`` / ``{% include %}`` / ``{% import %}`` /
   ``{% from %}`` target resolves to a template that actually exists. A missing
   target raises ``TemplateNotFound`` -> HTTP 500 at render time, which a
   compile-only pass would miss.

Pure static analysis: no app import, no DB, no network. Fast and deterministic.
"""
import os
import re

from jinja2 import Environment, FileSystemLoader, select_autoescape
from jinja2.exceptions import TemplateSyntaxError

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(REPO, "templates")

# Characters that must never appear in a code position (Jinja block or HTML
# attribute delimiter). Curly quotes are the primary offender; BOM and
# zero-width space are invisible gremlins that break line-1 tokenization.
SMART = {
    "‘": "LEFT SINGLE QUOTE",
    "’": "RIGHT SINGLE QUOTE",
    "“": "LEFT DOUBLE QUOTE",
    "”": "RIGHT DOUBLE QUOTE",
    "′": "PRIME",
    "″": "DOUBLE PRIME",
    "«": "LEFT GUILLEMET",
    "»": "RIGHT GUILLEMET",
    "﻿": "BYTE ORDER MARK",
    "​": "ZERO WIDTH SPACE",
}

# Spans of Jinja code: {{ ... }} expressions and {% ... %} statements.
_JINJA_SPAN = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)

# Static template references we can verify against the filesystem.
_REF = re.compile(r"""\{%-?\s*(extends|include|import|from)\s+(['"])(.+?)\2""")


def _iter_templates():
    """Yield (relname, abspath) for every *.html template, recursively."""
    for root, _dirs, files in os.walk(TEMPLATES_DIR):
        for f in sorted(files):
            if f.endswith(".html"):
                ap = os.path.join(root, f)
                rel = os.path.relpath(ap, TEMPLATES_DIR).replace(os.sep, "/")
                yield rel, ap


def _read(path):
    # utf-8 (NOT utf-8-sig) so a stray BOM stays visible and gets flagged.
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _make_env():
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html", "xml"]),
        # loopcontrols/do are harmless if unused; present so future templates
        # that use {% break %}/{% do %} still parse instead of false-failing.
        extensions=["jinja2.ext.loopcontrols", "jinja2.ext.do"],
    )


def _jinja_spans(src):
    return [(m.start(), m.end()) for m in _JINJA_SPAN.finditer(src)]


def _in_span(pos, spans):
    for a, b in spans:
        if a <= pos < b:
            return True
    return False


def test_templates_dir_present():
    """Sanity: we found the directory and it has templates to check."""
    names = [rel for rel, _ in _iter_templates()]
    assert names, "no *.html templates found under %r" % TEMPLATES_DIR


def test_every_template_compiles():
    """Every template must tokenize + compile (guards the {% extends %} 500)."""
    env = _make_env()
    failures = []
    for rel, ap in _iter_templates():
        src = _read(ap)
        try:
            env.compile(src, name=rel, filename=rel)
        except TemplateSyntaxError as e:
            failures.append("%s:%s: TemplateSyntaxError: %s"
                            % (rel, e.lineno, e.message))
        except Exception as e:  # pragma: no cover - defensive
            failures.append("%s: %s: %s" % (rel, type(e).__name__, e))
    assert not failures, (
        "%d template(s) failed to compile:\n  %s"
        % (len(failures), "\n  ".join(failures))
    )


def test_no_smart_quotes_in_code_positions():
    """No curly quote / BOM / ZWSP used as a Jinja or HTML-attribute delimiter.

    Curly quotes in visible prose are fine; this only rejects code-position uses
    (inside {{ }}/{% %}, or right after ``=`` as an attribute delimiter), plus
    any BOM/zero-width space anywhere.
    """
    offenders = []
    for rel, ap in _iter_templates():
        src = _read(ap)
        spans = _jinja_spans(src)
        for i, ch in enumerate(src):
            if ch not in SMART:
                continue
            name = SMART[ch]
            # BOM / ZWSP are never legitimate here — flag anywhere.
            always = ch in ("﻿", "​")
            in_jinja = _in_span(i, spans)
            # HTML attribute delimiter: char sits just after '=' (+ opt. spaces).
            j = i - 1
            while j >= 0 and src[j] in " \t":
                j -= 1
            attr = j >= 0 and src[j] == "="
            if always or in_jinja or attr:
                ln = src.count("\n", 0, i) + 1
                where = ("BOM/ZWSP" if always else
                         "JINJA-BLOCK" if in_jinja else "HTML-ATTR-DELIM")
                ctx = src[max(0, i - 30):i + 20].replace("\n", " ")
                offenders.append(
                    "%s:%s [%s as %s] ...%s..."
                    % (rel, ln, name, where, ascii(ctx))
                )
    assert not offenders, (
        "smart/curly quote (or BOM/ZWSP) used in a code position — this is the\n"
        "class of bug that 500'd /contacts/. Replace with ASCII quotes:\n  %s"
        % "\n  ".join(offenders)
    )


def test_static_template_references_resolve():
    """extends/include/import/from string targets must point at real files."""
    existing = {rel for rel, _ in _iter_templates()}
    # include/import/from can also target non-.html partials; collect all files.
    all_files = set()
    for root, _dirs, files in os.walk(TEMPLATES_DIR):
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), TEMPLATES_DIR)
            all_files.add(rel.replace(os.sep, "/"))
    missing = []
    for rel, ap in _iter_templates():
        src = _read(ap)
        for m in _REF.finditer(src):
            kw, target = m.group(1), m.group(3)
            ln = src.count("\n", 0, m.start()) + 1
            if target not in all_files:
                missing.append("%s:%s {%% %s '%s' %%} -> not found"
                               % (rel, ln, kw, target))
    assert not missing, (
        "%d template reference(s) point at a missing file "
        "(TemplateNotFound -> 500 at render):\n  %s"
        % (len(missing), "\n  ".join(missing))
    )
