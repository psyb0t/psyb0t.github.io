"""Build the psyb0t.github.io root site into a directory of static files.

The repo list, the top repos and the categories come from the profile repo's
scripts/update.py and categories.json, the same code and data that regenerate
the profile README, so both pages always show the same projects. This script
only adds what the README does not have: docs and blog links per repo, the
latest blog posts, and the files search engines read (sitemap.xml, robots.txt).

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
PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")

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
SITEMAP_INDEX = "sitemap.xml"
SITEMAP_ROOT = "sitemap-root.xml"
SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

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


def docs_url(name: str) -> str:
    return f"{SITE_URL}{name}/"


def live_doc_sitemaps(names: list[str]) -> list[str]:
    """Sitemaps of the project docs sites that actually serve one."""
    found = []
    for name in names:
        url = f"{docs_url(name)}sitemap.xml"
        try:
            http_get(url)
        except urllib.error.URLError as err:
            logger.info("docs sitemap missing", extra={"url": url, "err": str(err)})
            continue
        found.append(url)
    return found


def repo_links(repo: Any, extras: Extras | None) -> list[tuple[str, str]]:
    links = [("Repo", repo.url)]
    if extras is None:
        return links
    if extras.has_pages:
        links.append(("Docs", docs_url(repo.name)))
    if BLOG_HOST in extras.homepage:
        links.append(("Blog post", extras.homepage))
    elif extras.homepage:
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


def render_categories(categories: list[Any], extras: dict[str, Extras]) -> tuple[str, str]:
    nav = "".join(
        f'<li><a href="#{slug(c.title)}">{esc(c.title)}</a> <span class="count">{len(c.repos)}</span></li>'
        for c in categories
    )
    sections = "".join(
        f'<section class="category" id="{slug(c.title)}"><h3>{esc(c.title)}</h3><ul class="rows">'
        + "".join(render_row(r, extras.get(r.name)) for r in c.repos)
        + "</ul></section>"
        for c in categories
    )
    return nav, sections


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


def json_ld() -> str:
    data = {
        "@context": "https://schema.org",
        "@graph": [
            {
                "@type": "Person",
                "@id": f"{SITE_URL}#person",
                "name": PERSON["name"],
                "alternateName": PERSON["handle"],
                "url": SITE_URL,
                "email": f"mailto:{PERSON['email']}",
                "sameAs": [f"https://github.com/{USER}", BLOG_URL],
            },
            {
                "@type": "WebSite",
                "@id": f"{SITE_URL}#website",
                "url": SITE_URL,
                "name": f"{PERSON['name']} ({PERSON['handle']}): open source software",
                "author": {"@id": f"{SITE_URL}#person"},
            },
        ],
    }
    # A "</" inside the JSON would close the script element early.
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def render_page(
    top: list[Any],
    categories: list[Any],
    extras: dict[str, Extras],
    posts: list[Post],
    built: dt.datetime,
) -> str:
    projects = sum(len(c.repos) for c in categories)
    stars = sum(r.stars for c in categories for r in c.repos)
    nav, sections = render_categories(categories, extras)
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
    template = TEMPLATE.read_text(encoding="utf-8")
    missing = set(PLACEHOLDER.findall(template)) - values.keys()
    if missing:
        raise BuildError(f"template placeholders without a value: {sorted(missing)}")
    # One pass, so repo text that happens to contain {{...}} is left alone.
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], template)


def render_sitemaps(doc_sitemaps: list[str], built: dt.datetime) -> tuple[str, str]:
    lastmod = built.date().isoformat()
    root = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="{SITEMAP_NS}">\n'
        f"  <url><loc>{SITE_URL}</loc><lastmod>{lastmod}</lastmod></url>\n"
        "</urlset>\n"
    )
    entries = [f"{SITE_URL}{SITEMAP_ROOT}", *doc_sitemaps]
    index = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<sitemapindex xmlns="{SITEMAP_NS}">\n'
        + "".join(f"  <sitemap><loc>{esc(url)}</loc></sitemap>\n" for url in entries)
        + "</sitemapindex>\n"
    )
    return index, root


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
    posts = fetch_posts()
    listed = {r.name for c in categories for r in c.repos}
    doc_sitemaps = live_doc_sitemaps(sorted(n for n, e in extras.items() if e.has_pages and n in listed))
    built = dt.datetime.now(dt.UTC).replace(microsecond=0)

    page = render_page(top, categories, extras, posts, built)
    index, root = render_sitemaps(doc_sitemaps, built)

    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / SITEMAP_INDEX).write_text(index, encoding="utf-8")
    (out / SITEMAP_ROOT).write_text(root, encoding="utf-8")
    (out / "robots.txt").write_text(robots_txt(), encoding="utf-8")
    for path in STATIC_DIR.iterdir():
        if path.name != TEMPLATE.name:
            shutil.copy2(path, out / path.name)
    logger.info(
        "site built",
        extra={
            "out": str(out),
            "projects": len(listed),
            "top": len(top),
            "posts": len(posts),
            "doc_sitemaps": len(doc_sitemaps),
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
