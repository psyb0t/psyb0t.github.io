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
    is_fork: bool = False
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


def test_llms_txt_lists_links_posts_and_every_project_with_its_docs():
    categories = [
        FakeCategory("Trading", [FakeRepo("mt5-httpapi", description="MT5 over REST")]),
        FakeCategory("Other", [FakeRepo("bare", description="")]),
    ]
    extras = {"mt5-httpapi": build.Extras(homepage="", has_pages=True)}
    posts = build.parse_feed(FEED, limit=1)
    doc_llms = ["https://psyb0t.github.io/mt5-httpapi/llms.txt"]

    text = build.render_llms(categories, extras, posts, doc_llms)

    assert text.startswith("# Ciprian Mandache (psyb0t)\n\n> ")
    assert "- [Blog](https://ciprian.51k.eu/)" in text
    assert "- [First post](https://ciprian.51k.eu/first/): 2026-10-23" in text
    assert "## Trading" in text
    assert (
        "- [mt5-httpapi](https://github.com/psyb0t/mt5-httpapi): MT5 over REST. "
        "Docs: https://psyb0t.github.io/mt5-httpapi/. "
        "llms.txt: https://psyb0t.github.io/mt5-httpapi/llms.txt."
    ) in text
    assert "- [bare](https://github.com/psyb0t/bare)\n" in text


def test_listed_forks_are_marked_on_the_page_and_in_llms_txt():
    fork = FakeRepo("go-workflows", description="Durable workflows", is_fork=True)
    categories = [FakeCategory("Go", [fork])]

    assert '<span title="fork of another project">fork</span>' in page(categories)
    assert "- [go-workflows](https://github.com/psyb0t/go-workflows): Fork. Durable workflows." in (
        build.render_llms(categories, {}, [], [])
    )


DESCRIPTION = "Trading tools by Ciprian Mandache (psyb0t): brokers, market data and build tools."


def config(**categories) -> dict:
    return {
        "min_repos": 1,
        "categories": {
            title: {"slug": slug, "title": f"{title} page", "description": DESCRIPTION, "intro": "Intro."}
            for title, slug in categories.items()
        },
    }


def repos(count: int, prefix: str = "r") -> list[FakeRepo]:
    return [FakeRepo(f"{prefix}{i}", stars=count - i) for i in range(count)]


def test_every_configured_category_gets_one_page_and_unconfigured_ones_are_skipped(caplog):
    categories = [FakeCategory("Trading", repos(1)), FakeCategory("New", repos(2, "n"))]

    pages = build.plan_pages(categories, config(Trading="trading"))

    assert [p.slug for p in pages.all()] == ["trading"]
    assert pages.categories["Trading"].url == "https://psyb0t.github.io/projects/trading/"
    assert "category without a page" in caplog.text


def test_min_repos_leaves_small_categories_on_the_home_page_only():
    cfg = config(Trading="trading", Go="go") | {"min_repos": 3}
    categories = [FakeCategory("Trading", repos(3)), FakeCategory("Go", repos(2, "g"))]

    assert [p.slug for p in build.plan_pages(categories, cfg).all()] == ["trading"]


def test_duplicate_slugs_and_bad_descriptions_fail_the_build():
    categories = [FakeCategory("A", repos(1)), FakeCategory("B", repos(1, "b"))]
    with pytest.raises(build.BuildError, match="duplicate page slugs"):
        build.plan_pages(categories, config(A="same", B="same"))

    short = config(A="a")
    short["categories"]["A"]["description"] = "Too short."
    with pytest.raises(build.BuildError, match="characters"):
        build.plan_pages(categories[:1], short)

    anonymous = config(A="a")
    anonymous["categories"]["A"]["description"] = (
        "A description long enough to pass the length check, without a name."
    )
    with pytest.raises(build.BuildError, match="does not name"):
        build.plan_pages(categories[:1], anonymous)


def test_the_shipped_pages_json_is_valid_and_has_no_facets():
    shipped = build.load_pages_config()

    assert "facets" not in shipped
    for entry in shipped["categories"].values():
        build.check_page_text(entry["slug"], entry["description"])
    slugs = [e["slug"] for e in shipped["categories"].values()]
    assert len(slugs) == len(set(slugs))


def test_the_home_page_shows_the_first_rows_and_links_the_full_category_page():
    categories = [FakeCategory("Trading", repos(7)), FakeCategory("Go", repos(2, "g"))]
    pages = build.plan_pages(categories, config(Trading="trading", Go="go"))

    html = build.render_page([], categories, {}, [], BUILT, pages)

    trading = html[html.index('id="trading"') : html.index('id="go"')]
    assert trading.count("<li>") == build.HOME_ROWS
    assert 'href="https://psyb0t.github.io/projects/trading/">All 7 projects: Trading page</a>' in trading
    go = html[html.index('id="go"') :]
    assert '<h3><a href="https://psyb0t.github.io/projects/go/">Go</a></h3>' in go
    assert "All 2 projects" not in go


def test_a_category_page_lists_every_repo_with_its_own_title_canonical_and_json_ld():
    categories = [FakeCategory("Trading", repos(7)), FakeCategory("Go", repos(2, "g"))]
    pages = build.plan_pages(categories, config(Trading="trading", Go="go"))

    html = build.render_list_page(pages.categories["Trading"], pages, {}, BUILT)

    assert "<title>Trading page by Ciprian Mandache (psyb0t)</title>" in html
    assert '<link rel="canonical" href="https://psyb0t.github.io/projects/trading/">' in html
    assert html.count("<h1>") == 1
    assert html.count('class="name"') == 7
    assert 'href="https://psyb0t.github.io/projects/go/">Go page</a>' in html
    assert 'href="https://psyb0t.github.io/projects/trading/">Trading page</a>' not in html
    assert 'href="/style.css"' in html
    blob = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)
    assert blob
    graph = json.loads(blob.group(1))["@graph"]
    collection = next(g for g in graph if g["@type"] == "CollectionPage")
    assert collection["mainEntity"]["numberOfItems"] == 7
    crumbs = next(g for g in graph if g["@type"] == "BreadcrumbList")
    assert [c["name"] for c in crumbs["itemListElement"]] == [
        "Ciprian Mandache (psyb0t)",
        "Projects",
        "Trading page",
    ]


def test_the_projects_index_links_every_page():
    categories = [FakeCategory("Trading", repos(1)), FakeCategory("Go", repos(2, "g"))]
    pages = build.plan_pages(categories, config(Trading="trading", Go="go"))

    html = build.render_projects_index(pages, BUILT)

    assert '<link rel="canonical" href="https://psyb0t.github.io/projects/">' in html
    assert ">1 project<" in html and ">2 projects<" in html
    assert "By type" not in html


def test_sitemap_and_llms_txt_list_the_project_pages():
    categories = [FakeCategory("Trading", repos(1))]
    pages = build.plan_pages(categories, config(Trading="trading"))

    _, root = build.render_sitemaps([], BUILT, pages)
    text = build.render_llms(categories, {}, [], [], pages)

    assert "<loc>https://psyb0t.github.io/projects/</loc>" in root
    assert "<loc>https://psyb0t.github.io/projects/trading/</loc>" in root
    assert f"- [Trading page](https://psyb0t.github.io/projects/trading/): {DESCRIPTION}" in text


def test_build_writes_the_pages_and_never_copies_templates(monkeypatch, tmp_path):
    categories = [FakeCategory("Trading", repos(2))]

    class FakeUpdate:
        PER_PAGE = 100

        class UpdateError(Exception):
            pass

        @staticmethod
        def fetch_repos(user):
            return categories[0].repos

        @staticmethod
        def api_get(path):
            return [{"name": r.name, "homepage": "", "has_pages": False} for r in categories[0].repos]

        @staticmethod
        def load_categories():
            return {}

        @staticmethod
        def categorize(repos_, cfg):
            return categories

        @staticmethod
        def top_repos(repos_):
            return repos_[:1]

    monkeypatch.setattr(build, "load_pages_config", lambda: config(Trading="trading"))
    monkeypatch.setattr(build, "fetch_posts", lambda: [])
    out = tmp_path / "site"

    build.build(FakeUpdate, out)

    assert (out / "index.html").is_file()
    assert (out / "projects" / "index.html").is_file()
    assert (out / "projects" / "trading" / "index.html").is_file()
    assert not list(out.glob("*.template.html"))
    assert (out / "style.css").is_file()


def test_a_homepage_that_is_the_docs_site_is_not_linked_twice():
    extras = build.Extras(homepage="https://psyb0t.github.io/x", has_pages=True)

    assert build.repo_links(FakeRepo("x"), extras) == [
        ("Repo", "https://github.com/psyb0t/x"),
        ("Docs", "https://psyb0t.github.io/x/"),
    ]


def test_placeholders_with_digits_are_filled():
    categories = [FakeCategory("Trading", repos(1))]
    pages = build.plan_pages(categories, config(Trading="trading"))

    html = build.render_list_page(pages.categories["Trading"], pages, {}, BUILT)

    assert "<h1>Trading page</h1>" in html
    assert "{{" not in html
