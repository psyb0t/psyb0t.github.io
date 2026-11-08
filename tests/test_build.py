import datetime as dt
import importlib.util
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("build", ROOT / "scripts" / "build.py")
assert _spec and _spec.loader
build = importlib.util.module_from_spec(_spec)
sys.modules["build"] = build
_spec.loader.exec_module(build)

BUILT = dt.datetime(2026, 1, 2, 3, 4, tzinfo=dt.UTC)


@dataclass
class FakeRepo:
    name: str
    description: str = "does a thing"
    language: str = "Go"
    stars: int = 0
    forks: int = 0
    url: str = ""

    def __post_init__(self) -> None:
        self.url = self.url or f"https://github.com/psyb0t/{self.name}"


@dataclass
class FakeCategory:
    title: str
    repos: list[FakeRepo] = field(default_factory=list)


FEED = b"""<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>First post</title><link>https://ciprian.51k.eu/first/</link>
<pubDate>Fri, 23 Oct 2026 10:00:00 +0000</pubDate></item>
<item><title>No date</title><link>https://ciprian.51k.eu/nodate/</link></item>
<item><title>Second post</title><link>https://ciprian.51k.eu/second/</link>
<pubDate>Thu, 15 Oct 2026 09:30:00 +0000</pubDate></item>
<item><title>Third post</title><link>https://ciprian.51k.eu/third/</link>
<pubDate>Wed, 14 Oct 2026 09:30:00 +0000</pubDate></item>
</channel></rss>"""


def page(categories, extras=None, top=None, posts=None) -> str:
    return build.render_page(top or [], categories, extras or {}, posts or [], BUILT)


def test_repo_links_add_docs_and_a_blog_post_from_the_repo_website():
    repo = FakeRepo("mt5-httpapi")
    extras = build.Extras(homepage="https://ciprian.51k.eu/mt5-httpapi-post/", has_pages=True)

    assert build.repo_links(repo, extras) == [
        ("Repo", "https://github.com/psyb0t/mt5-httpapi"),
        ("Docs", "https://psyb0t.github.io/mt5-httpapi/"),
        ("Blog post", "https://ciprian.51k.eu/mt5-httpapi-post/"),
    ]


def test_a_website_off_the_blog_is_labelled_website():
    extras = build.Extras(homepage="https://pypi.org/project/x/", has_pages=False)

    assert build.repo_links(FakeRepo("x"), extras)[1] == ("Website", "https://pypi.org/project/x/")


def test_a_repo_without_extras_links_only_to_itself():
    assert build.repo_links(FakeRepo("x"), None) == [("Repo", "https://github.com/psyb0t/x")]


def test_the_page_lists_every_category_with_its_repos_and_totals():
    categories = [
        FakeCategory("Trading", [FakeRepo("a", stars=3), FakeRepo("b", stars=1)]),
        FakeCategory("Go libraries and tooling", [FakeRepo("c", stars=2)]),
    ]

    html = page(categories)

    assert 'href="#trading"' in html and 'id="trading"' in html
    assert 'id="go-libraries-and-tooling"' in html
    assert "3 public projects in 2 categories, 6 stars between them." in html
    assert html.count("<h1>") == 1


def test_repo_text_is_escaped_and_never_read_as_a_placeholder():
    repo = FakeRepo("x", description='<script>alert(1)</script> renders {{json_ld}} & "quotes"')

    html = page([FakeCategory("Other", [repo])])

    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt; renders {{json_ld}} &amp; &quot;quotes&quot;" in html


def test_the_json_ld_names_the_person_and_parses():
    html = page([FakeCategory("Other", [FakeRepo("x")])])

    blob = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    assert blob
    person = json.loads(blob.group(1))["@graph"][0]
    assert person["name"] == "Ciprian Mandache"
    assert person["alternateName"] == "psyb0t"
    assert person["email"] == "mailto:psyb0t@51k.eu"


def test_the_page_carries_no_location():
    html = page([FakeCategory("Other", [FakeRepo("x")])]).lower()

    assert "location" not in html and "addresslocality" not in html


def test_a_missing_template_value_fails_the_build(monkeypatch, tmp_path):
    template = tmp_path / "t.html"
    template.write_text("{{json_ld}} {{nope}}", encoding="utf-8")
    monkeypatch.setattr(build, "TEMPLATE", template)

    with pytest.raises(build.BuildError, match="nope"):
        page([])


def test_the_feed_keeps_dated_posts_up_to_the_limit():
    posts = build.parse_feed(FEED, limit=2)

    assert [(p.title, p.published.isoformat()) for p in posts] == [
        ("First post", "2026-10-23"),
        ("Second post", "2026-10-15"),
    ]


def test_an_unreadable_feed_leaves_the_blog_section_out(monkeypatch):
    def broken(url):
        raise build.urllib.error.URLError("down")

    monkeypatch.setattr(build, "http_get", broken)

    assert build.fetch_posts() == []
    assert build.render_posts([]) == ""


def test_the_sitemap_index_points_at_the_root_and_each_docs_site():
    index, root = build.render_sitemaps(["https://psyb0t.github.io/peen/sitemap.xml"], BUILT)

    assert "<loc>https://psyb0t.github.io/sitemap-root.xml</loc>" in index
    assert "<loc>https://psyb0t.github.io/peen/sitemap.xml</loc>" in index
    assert "<loc>https://psyb0t.github.io/</loc><lastmod>2026-01-02</lastmod>" in root


def test_only_docs_sites_that_serve_a_sitemap_are_listed(monkeypatch):
    def fake_get(url):
        if "/gone/" in url:
            raise build.urllib.error.HTTPError(url, 404, "Not Found", None, None)
        return b"<urlset/>"

    monkeypatch.setattr(build, "http_get", fake_get)

    assert build.live_doc_sitemaps(["peen", "gone"]) == ["https://psyb0t.github.io/peen/sitemap.xml"]


def test_robots_txt_names_the_sitemap_index():
    assert "Sitemap: https://psyb0t.github.io/sitemap.xml" in build.robots_txt()
