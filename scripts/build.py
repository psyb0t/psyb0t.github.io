"""Build the psyb0t.github.io root site into a directory of static files.

The repo list, the top repos and the categories come from the profile repo's
scripts/update.py and categories.json, the same code and data that regenerate
the profile README, so both pages always show the same projects. This script
only adds what the README does not have: docs and blog links per repo, the
latest blog posts, and the files search engines read (sitemap.xml, robots.txt).
pages.json adds a page per category under /projects/, so each topic has its
own URL and every project sits on exactly one of those pages.

Reads GITHUB_TOKEN when set. Usage:
  python3 scripts/build.py --profile-dir ../psyb0t --out _site
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import importlib.util
import json
import logging
import re
import shutil
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "static"
TEMPLATE = ROOT / "static" / "index.template.html"
LIST_TEMPLATE = ROOT / "static" / "list.template.html"
TEMPLATE_SUFFIX = ".template.html"
PAGES_CONFIG = ROOT / "pages.json"
PLACEHOLDER = re.compile(r"\{\{([a-z0-9_]+)\}\}")

USER = "psyb0t"
SITE_URL = "https://psyb0t.github.io/"
BLOG_URL = "https://ciprian.51k.eu/"
BLOG_HOST = "ciprian.51k.eu"
FEED_URL = "https://ciprian.51k.eu/feed/"
FEED_POSTS = 5
HTTP_TIMEOUT_SECONDS = 30
USER_AGENT = "psyb0t.github.io-build"

# The root sitemap lists this page, and the sitemap index points at it plus
# every project docs site that serves its own sitemap.
LLMS_TXT = "llms.txt"
SUMMARY = (
    "Docker images, MCP servers, MetaTrader and IBKR APIs, AI agent tooling and Go "
    "libraries I actually run. Every public repo, sorted by category."
)
SITEMAP_INDEX = "sitemap.xml"
SITEMAP_ROOT = "sitemap-root.xml"
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

# Category pages live under /projects/ because project docs sites
# already own /<repo>/. The home page shows this many repos per category that
# has its own page, then links to it.
PROJECTS_DIR = "projects"
HOME_ROWS = 5
DESCRIPTION_MIN = 50
DESCRIPTION_MAX = 170

PERSON = {
    "name": "Ciprian Mandache",
    "handle": "psyb0t",
    "email": "psyb0t@51k.eu",
}
LINKS = [
    ("GitHub", f"https://github.com/{USER}"),
    ("Blog", BLOG_URL),
    ("Email", f"mailto:{PERSON['email']}"),
    ("GitHub Sponsors", f"https://github.com/sponsors/{USER}"),
    ("Buy Me a Coffee", f"https://buymeacoffee.com/{USER}"),
    ("Monero", f"https://github.com/{USER}/sponsors#monero-xmr"),
]


class BuildError(Exception):
    """The site could not be built; nothing is written to the output directory."""


@dataclass
class Extras:
    """Repo fields the profile's Repo dataclass does not carry."""

    homepage: str
    has_pages: bool


@dataclass
class ListPage:
    """One category page under /projects/<slug>/."""

    slug: str
    title: str
    description: str
    intro: str
    repos: list[Any]

    @property
    def url(self) -> str:
        return f"{SITE_URL}{PROJECTS_DIR}/{self.slug}/"


@dataclass
class Pages:
    """The /projects/ pages, keyed by category title, in category order."""

    categories: dict[str, ListPage]

    def all(self) -> list[ListPage]:
        return list(self.categories.values())


NO_PAGES = Pages(categories={})


@dataclass
class Post:
    title: str
    url: str
    published: dt.date


def load_update(profile_dir: Path) -> ModuleType:
    """Import the profile repo's scripts/update.py as a module."""
    path = profile_dir / "scripts" / "update.py"
    spec = importlib.util.spec_from_file_location("profile_update", path)
    if spec is None or spec.loader is None:
        raise BuildError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves annotations through sys.modules.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except OSError as err:
        raise BuildError(f"cannot load {path}") from err
    return module


def fetch_extras(update: ModuleType) -> dict[str, Extras]:
    raw = update.api_get(f"/users/{USER}/repos?type=owner&per_page={update.PER_PAGE}")
    return {
        r["name"]: Extras(homepage=r.get("homepage") or "", has_pages=bool(r.get("has_pages"))) for r in raw
    }


def http_get(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        body: bytes = response.read()
        return body


def fetch_posts(url: str = FEED_URL, limit: int = FEED_POSTS) -> list[Post]:
    """The latest blog posts, or none when the feed cannot be read: a blog
    outage should not stop the project list from updating."""
    try:
        return parse_feed(http_get(url), limit)
    except (urllib.error.URLError, ET.ParseError, ValueError) as err:
        logger.warning("blog feed skipped", extra={"url": url, "err": str(err)})
        return []


def parse_feed(body: bytes, limit: int) -> list[Post]:
    posts = []
    for item in ET.fromstring(body).iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = item.findtext("pubDate") or ""
        if not title or not link or not published:
            continue
        date = dt.datetime.strptime(published.strip(), "%a, %d %b %Y %H:%M:%S %z").date()
        posts.append(Post(title=title, url=link, published=date))
        if len(posts) == limit:
            break
    logger.info("blog posts fetched", extra={"count": len(posts)})
    return posts


def load_pages_config(path: Path = PAGES_CONFIG) -> dict[str, Any]:
    try:
        config: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise BuildError(f"read {path.name}") from err
    return config


def check_page_text(slug: str, description: str) -> None:
    """Every page names me in its description and fits the snippet length."""
    if not DESCRIPTION_MIN <= len(description) <= DESCRIPTION_MAX:
        raise BuildError(f"{slug}: description is {len(description)} characters")
    if PERSON["name"] not in description:
        raise BuildError(f"{slug}: description does not name {PERSON['name']}")


def plan_pages(categories: list[Any], config: dict[str, Any]) -> Pages:
    """A page for every configured category with at least `min_repos` repos. A
    category without an entry in pages.json is skipped with a warning, so a new
    category in the profile repo never breaks the scheduled build."""
    min_repos = int(config.get("min_repos", 1))
    category_config: dict[str, dict[str, str]] = config.get("categories", {})
    by_title: dict[str, ListPage] = {}
    for category in categories:
        entry = category_config.get(category.title)
        if entry is None:
            logger.warning("category without a page", extra={"category": category.title})
            continue
        if len(category.repos) < min_repos:
            continue
        by_title[category.title] = ListPage(
            slug=entry["slug"],
            title=entry["title"],
            description=entry["description"],
            intro=entry["intro"],
            repos=category.repos,
        )

    pages = Pages(categories=by_title)
    slugs = [p.slug for p in pages.all()]
    if len(slugs) != len(set(slugs)):
        raise BuildError(f"duplicate page slugs in {PAGES_CONFIG.name}: {sorted(slugs)}")
    for page in pages.all():
        check_page_text(page.slug, page.description)
    return pages


def docs_url(name: str) -> str:
    return f"{SITE_URL}{name}/"


def live_doc_files(names: list[str], filename: str) -> list[str]:
    """URLs of `filename` on the project docs sites that actually serve it."""
    found = []
    for name in names:
        url = f"{docs_url(name)}{filename}"
        try:
            http_get(url)
        except urllib.error.URLError as err:
            logger.info("docs file missing", extra={"url": url, "err": str(err)})
            continue
        found.append(url)
    return found


def live_doc_sitemaps(names: list[str]) -> list[str]:
    """Sitemaps of the project docs sites that actually serve one."""
    return live_doc_files(names, "sitemap.xml")


def repo_links(repo: Any, extras: Extras | None) -> list[tuple[str, str]]:
    links = [("Repo", repo.url)]
    if extras is None:
        return links
    if extras.has_pages:
        links.append(("Docs", docs_url(repo.name)))
    if BLOG_HOST in extras.homepage:
        links.append(("Blog post", extras.homepage))
    elif extras.homepage and extras.homepage.rstrip("/") != docs_url(repo.name).rstrip("/"):
        # A homepage that is the docs site is already linked as Docs.
        links.append(("Website", extras.homepage))
    return links


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def slug(title: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in title.lower()).strip("-").replace("--", "-")


def render_links(links: list[tuple[str, str]], name: str) -> str:
    return " ".join(
        f'<a href="{esc(url)}" aria-label="{esc(name)}: {esc(label)}">{esc(label)}</a>'
        for label, url in links
    )


def render_facts(repo: Any) -> str:
    facts = []
    if repo.language:
        facts.append(f'<span class="lang">{esc(repo.language)}</span>')
    if getattr(repo, "is_fork", False):
        facts.append('<span title="fork of another project">fork</span>')
    if repo.stars:
        facts.append(f'<span title="stars">★ {repo.stars}</span>')
    if repo.forks:
        facts.append(f'<span title="forks">{repo.forks} {"fork" if repo.forks == 1 else "forks"}</span>')
    return " ".join(facts)


def render_card(repo: Any, extras: Extras | None) -> str:
    return (
        '<article class="card">'
        f'<h3><a href="{esc(repo.url)}">{esc(repo.name)}</a></h3>'
        f"<p>{esc(repo.description)}</p>"
        f'<div class="meta"><span class="facts">{render_facts(repo)}</span>'
        f'<span class="links">{render_links(repo_links(repo, extras), repo.name)}</span></div>'
        "</article>"
    )


def render_row(repo: Any, extras: Extras | None) -> str:
    return (
        "<li>"
        f'<div class="row-head"><a class="name" href="{esc(repo.url)}">{esc(repo.name)}</a>'
        f'<span class="facts">{render_facts(repo)}</span></div>'
        f"<p>{esc(repo.description)}</p>"
        f'<div class="links">{render_links(repo_links(repo, extras), repo.name)}</div>'
        "</li>"
    )


def render_rows(repos: list[Any], extras: dict[str, Extras]) -> str:
    return "".join(render_row(r, extras.get(r.name)) for r in repos)


def render_categories(
    categories: list[Any], extras: dict[str, Extras], pages: Pages = NO_PAGES
) -> tuple[str, str]:
    nav = "".join(
        f'<li><a href="#{slug(c.title)}">{esc(c.title)}</a> <span class="count">{len(c.repos)}</span></li>'
        for c in categories
    )
    sections = []
    for c in categories:
        page = pages.categories.get(c.title)
        heading = f'<a href="{esc(page.url)}">{esc(c.title)}</a>' if page else esc(c.title)
        shown = c.repos[:HOME_ROWS] if page else c.repos
        more = (
            f'<p class="more"><a href="{esc(page.url)}">All {len(c.repos)} projects: '
            f"{esc(page.title)}</a></p>"
            if page and len(c.repos) > len(shown)
            else ""
        )
        sections.append(
            f'<section class="category" id="{slug(c.title)}"><h3>{heading}</h3><ul class="rows">'
            + render_rows(shown, extras)
            + f"</ul>{more}</section>"
        )
    return nav, "".join(sections)


def render_posts(posts: list[Post]) -> str:
    if not posts:
        return ""
    items = "".join(
        f'<li><time datetime="{p.published.isoformat()}">{p.published.isoformat()}</time> '
        f'<a href="{esc(p.url)}">{esc(p.title)}</a></li>'
        for p in posts
    )
    return (
        '<section id="blog"><h2>Latest from the blog</h2>'
        f'<ul class="posts">{items}</ul>'
        f'<p><a href="{BLOG_URL}">All posts on {BLOG_HOST}</a></p></section>'
    )


PERSON_ID = f"{SITE_URL}#person"
WEBSITE_ID = f"{SITE_URL}#website"
BYLINE = f"{PERSON['name']} ({PERSON['handle']})"


def person_and_site() -> list[dict[str, Any]]:
    return [
        {
            "@type": "Person",
            "@id": PERSON_ID,
            "name": PERSON["name"],
            "alternateName": PERSON["handle"],
            "url": SITE_URL,
            "email": f"mailto:{PERSON['email']}",
            "sameAs": [f"https://github.com/{USER}", BLOG_URL],
        },
        {
            "@type": "WebSite",
            "@id": WEBSITE_ID,
            "url": SITE_URL,
            "name": f"{BYLINE}: open source software",
            "author": {"@id": PERSON_ID},
        },
    ]


def dump_json_ld(graph: list[dict[str, Any]]) -> str:
    data = {"@context": "https://schema.org", "@graph": graph}
    # A "</" inside the JSON would close the script element early.
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def json_ld() -> str:
    return dump_json_ld(person_and_site())


def breadcrumbs(trail: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "@type": "BreadcrumbList",
        "itemListElement": [
            {"@type": "ListItem", "position": i, "name": name, "item": url}
            for i, (name, url) in enumerate(trail, start=1)
        ],
    }


def list_json_ld(page: ListPage, trail: list[tuple[str, str]]) -> str:
    collection = {
        "@type": "CollectionPage",
        "@id": f"{page.url}#page",
        "url": page.url,
        "name": f"{page.title} by {BYLINE}",
        "description": page.description,
        "isPartOf": {"@id": WEBSITE_ID},
        "author": {"@id": PERSON_ID},
        "mainEntity": {
            "@type": "ItemList",
            "numberOfItems": len(page.repos),
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": i,
                    "item": {
                        "@type": "SoftwareSourceCode",
                        "name": r.name,
                        "url": r.url,
                        "codeRepository": r.url,
                        **({"description": r.description} if r.description else {}),
                        **({"programmingLanguage": r.language} if r.language else {}),
                        "author": {"@id": PERSON_ID},
                    },
                }
                for i, r in enumerate(page.repos, start=1)
            ],
        },
    }
    return dump_json_ld([*person_and_site(), collection, breadcrumbs(trail)])


def index_json_ld(pages: Pages, url: str, description: str, trail: list[tuple[str, str]]) -> str:
    collection = {
        "@type": "CollectionPage",
        "@id": f"{url}#page",
        "url": url,
        "name": f"Projects by {BYLINE}",
        "description": description,
        "isPartOf": {"@id": WEBSITE_ID},
        "author": {"@id": PERSON_ID},
        "hasPart": [{"@type": "CollectionPage", "url": p.url, "name": p.title} for p in pages.all()],
    }
    return dump_json_ld([*person_and_site(), collection, breadcrumbs(trail)])


def fill(template_path: Path, values: dict[str, str]) -> str:
    template = template_path.read_text(encoding="utf-8")
    missing = set(PLACEHOLDER.findall(template)) - values.keys()
    if missing:
        raise BuildError(f"template placeholders without a value: {sorted(missing)}")
    # One pass, so repo text that happens to contain {{...}} is left alone.
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def render_page(
    top: list[Any],
    categories: list[Any],
    extras: dict[str, Extras],
    posts: list[Post],
    built: dt.datetime,
    pages: Pages = NO_PAGES,
) -> str:
    projects = sum(len(c.repos) for c in categories)
    stars = sum(r.stars for c in categories for r in c.repos)
    nav, sections = render_categories(categories, extras, pages)
    values = {
        "json_ld": json_ld(),
        "links": "".join(f'<li><a href="{esc(url)}">{esc(label)}</a></li>' for label, url in LINKS),
        "projects": str(projects),
        "stars": str(stars),
        "category_count": str(len(categories)),
        "top_cards": "".join(render_card(r, extras.get(r.name)) for r in top),
        "category_nav": nav,
        "category_sections": sections,
        "posts": render_posts(posts),
        "built": built.strftime("%Y-%m-%d %H:%M UTC"),
        "built_iso": built.isoformat(),
    }
    return fill(TEMPLATE, values)


PROJECTS_URL = f"{SITE_URL}{PROJECTS_DIR}/"
PROJECTS_DESCRIPTION = (
    f"Every open source project by {BYLINE}, one page per topic: AI coding agents, "
    "self-hosted services, trading APIs, Go libraries and more."
)


def render_related(pages: Pages, current: str = "") -> str:
    items = "".join(
        f'<li><a href="{esc(p.url)}">{esc(p.title)}</a> <span class="count">{len(p.repos)}</span></li>'
        for p in pages.all()
        if p.slug != current
    )
    return (
        '<section id="related"><h2>More projects by me</h2>'
        f'<ul class="category-nav">{items}</ul>'
        f'<p><a href="{SITE_URL}">Back to {esc(BYLINE)}</a></p></section>'
    )


def render_breadcrumb(trail: list[tuple[str, str]]) -> str:
    links = [f'<a href="{esc(url)}">{esc(name)}</a>' for name, url in trail[:-1]]
    links.append(f'<span aria-current="page">{esc(trail[-1][0])}</span>')
    return f'<nav class="breadcrumb" aria-label="Breadcrumb">{" / ".join(links)}</nav>'


def list_values(
    title: str,
    description: str,
    url: str,
    json_ld_text: str,
    trail: list[tuple[str, str]],
    built: dt.datetime,
) -> dict[str, str]:
    return {
        "title_tag": esc(f"{title} by {BYLINE}"),
        "h1": esc(title),
        "description": esc(description),
        "canonical": esc(url),
        "json_ld": json_ld_text,
        "breadcrumb": render_breadcrumb(trail),
        "byline": f'Open source projects by <a href="{SITE_URL}">{esc(BYLINE)}</a>.',
        "built": built.strftime("%Y-%m-%d %H:%M UTC"),
        "built_iso": built.isoformat(),
    }


def render_list_page(page: ListPage, pages: Pages, extras: dict[str, Extras], built: dt.datetime) -> str:
    trail = [(BYLINE, SITE_URL), ("Projects", PROJECTS_URL), (page.title, page.url)]
    stars = sum(r.stars for r in page.repos)
    body = (
        f'<p class="stats">{len(page.repos)} projects, {stars} stars between them.</p>'
        f'<ul class="rows">{render_rows(page.repos, extras)}</ul>'
    )
    values = list_values(page.title, page.description, page.url, list_json_ld(page, trail), trail, built)
    values |= {
        "intro": f"<p>{esc(page.intro)}</p>",
        "body": body,
        "related": render_related(pages, page.slug),
    }
    return fill(LIST_TEMPLATE, values)


def render_projects_index(pages: Pages, built: dt.datetime) -> str:
    trail = [(BYLINE, SITE_URL), ("Projects", PROJECTS_URL)]

    rows = "".join(
        f'<li><div class="row-head"><a class="name" href="{esc(p.url)}">{esc(p.title)}</a>'
        f'<span class="facts"><span>{len(p.repos)} {"project" if len(p.repos) == 1 else "projects"}</span>'
        f"</span></div><p>{esc(p.description)}</p></li>"
        for p in pages.all()
    )

    values = list_values(
        "Projects",
        PROJECTS_DESCRIPTION,
        PROJECTS_URL,
        index_json_ld(pages, PROJECTS_URL, PROJECTS_DESCRIPTION, trail),
        trail,
        built,
    )
    values |= {
        "intro": (
            "<p>Every public project I maintain, one page per topic. "
            f'The <a href="{SITE_URL}">home page</a> has the most starred ones and my latest blog posts.</p>'
        ),
        "body": f'<ul class="rows">{rows}</ul>',
        "related": "",
    }
    return fill(LIST_TEMPLATE, values)


def render_sitemaps(doc_sitemaps: list[str], built: dt.datetime, pages: Pages = NO_PAGES) -> tuple[str, str]:
    lastmod = built.date().isoformat()
    urls = [SITE_URL, *([PROJECTS_URL] if pages.all() else []), *(p.url for p in pages.all())]
    root = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="{SITEMAP_NS}">\n'
        + "".join(f"  <url><loc>{esc(url)}</loc><lastmod>{lastmod}</lastmod></url>\n" for url in urls)
        + "</urlset>\n"
    )
    entries = [f"{SITE_URL}{SITEMAP_ROOT}", *doc_sitemaps]
    index = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<sitemapindex xmlns="{SITEMAP_NS}">\n'
        + "".join(f"  <sitemap><loc>{esc(url)}</loc></sitemap>\n" for url in entries)
        + "</sitemapindex>\n"
    )
    return index, root


def render_llms(
    categories: list[Any],
    extras: dict[str, Extras],
    posts: list[Post],
    doc_llms: list[str],
    pages: Pages = NO_PAGES,
) -> str:
    """The llms.txt index: who I am, where to find me, and every project by category."""
    have_llms = set(doc_llms)
    lines = [
        f"# {PERSON['name']} ({PERSON['handle']})",
        "",
        f"> {SUMMARY}",
        "",
        f"Every public repository I maintain on github.com/{USER}; listed forks say so. "
        "Each entry links the "
        "repository; projects with a documentation site also link it, and its own llms.txt "
        "when it has one.",
        "",
        "## Links",
        "",
        *(f"- [{label}]({url})" for label, url in LINKS),
    ]
    if posts:
        lines += ["", "## Latest blog posts", ""]
        lines += [f"- [{p.title}]({p.url}): {p.published.isoformat()}" for p in posts]
    if pages.all():
        lines += ["", "## Project pages", ""]
        lines += [f"- [{p.title}]({p.url}): {p.description}" for p in pages.all()]
    for category in categories:
        lines += ["", f"## {category.title}", ""]
        for repo in category.repos:
            notes = ["Fork."] if getattr(repo, "is_fork", False) else []
            notes += [repo.description.strip()] if repo.description else []
            extra = extras.get(repo.name)
            if extra and extra.has_pages:
                notes.append(f"Docs: {docs_url(repo.name)}")
                if f"{docs_url(repo.name)}{LLMS_TXT}" in have_llms:
                    notes.append(f"llms.txt: {docs_url(repo.name)}{LLMS_TXT}")
            note = " ".join(n if n.endswith(".") else f"{n}." for n in notes)
            lines.append(f"- [{repo.name}]({repo.url})" + (f": {note}" if note else ""))
    return "\n".join(lines) + "\n"


def robots_txt() -> str:
    return f"User-agent: *\nAllow: /\n\nSitemap: {SITE_URL}{SITEMAP_INDEX}\n"


def build(update: ModuleType, out: Path) -> None:
    try:
        repos = update.fetch_repos(USER)
        extras = fetch_extras(update)
        categories = update.categorize(repos, update.load_categories())
    except update.UpdateError as err:
        raise BuildError("read repos from the GitHub API") from err
    top = update.top_repos(repos)
    pages = plan_pages(categories, load_pages_config())
    posts = fetch_posts()
    listed = {r.name for c in categories for r in c.repos}
    doc_sites = sorted(n for n, e in extras.items() if e.has_pages and n in listed)
    doc_sitemaps = live_doc_sitemaps(doc_sites)
    doc_llms = live_doc_files(doc_sites, LLMS_TXT)
    built = dt.datetime.now(dt.UTC).replace(microsecond=0)

    page = render_page(top, categories, extras, posts, built, pages)
    index, root = render_sitemaps(doc_sitemaps, built, pages)

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / SITEMAP_INDEX).write_text(index, encoding="utf-8")
    (out / SITEMAP_ROOT).write_text(root, encoding="utf-8")
    (out / "robots.txt").write_text(robots_txt(), encoding="utf-8")
    (out / LLMS_TXT).write_text(render_llms(categories, extras, posts, doc_llms, pages), encoding="utf-8")
    projects = out / PROJECTS_DIR
    projects.mkdir()
    (projects / "index.html").write_text(render_projects_index(pages, built), encoding="utf-8")
    for list_page in pages.all():
        (projects / list_page.slug).mkdir()
        (projects / list_page.slug / "index.html").write_text(
            render_list_page(list_page, pages, extras, built), encoding="utf-8"
        )
    for path in STATIC_DIR.iterdir():
        if not path.name.endswith(TEMPLATE_SUFFIX):
            shutil.copy2(path, out / path.name)
    logger.info(
        "site built",
        extra={
            "out": str(out),
            "projects": len(listed),
            "top": len(top),
            "pages": len(pages.all()),
            "posts": len(posts),
            "doc_sitemaps": len(doc_sitemaps),
            "doc_llms": len(doc_llms),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--profile-dir", type=Path, required=True, help="checkout of psyb0t/psyb0t")
    parser.add_argument("--out", type=Path, default=ROOT / "_site", help="output directory (replaced)")
    args = parser.parse_args()

    try:
        update = load_update(args.profile_dir)
    except BuildError as err:
        sys.exit(f"build failed: {err}")
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(update.JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    try:
        build(update, args.out)
    except BuildError as err:
        logger.error("build failed", extra={"err": str(err), "cause": str(err.__cause__)})
        sys.exit(1)


if __name__ == "__main__":
    main()
