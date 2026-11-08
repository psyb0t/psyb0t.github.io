# psyb0t.github.io

Source of [psyb0t.github.io](https://psyb0t.github.io/): who I am, my most starred repos, my latest blog posts and every public project I have, sorted by category.

Nothing on the page is written by hand except the about text. The repo list comes from the GitHub API through the same code that regenerates my [profile README](https://github.com/psyb0t/psyb0t): [`scripts/update.py`](https://github.com/psyb0t/psyb0t/blob/master/scripts/update.py) fetches the repos, and [`categories.json`](https://github.com/psyb0t/psyb0t/blob/master/categories.json) sorts them. To move a repo to another category, edit `categories.json` there. This site picks it up on its next build.

## What the build writes

[`scripts/build.py`](scripts/build.py) imports the profile's `update.py` and writes `_site/`:

- `index.html`, from [`static/index.template.html`](static/index.template.html) and [`static/style.css`](static/style.css). Each repo links to its docs site when it has GitHub Pages enabled, and to its blog post when the repo's website field points at [ciprian.51k.eu](https://ciprian.51k.eu/).
- `sitemap.xml`, a sitemap index: `sitemap-root.xml` for this page plus the `sitemap.xml` of every project docs site under psyb0t.github.io that serves one. Submit this one URL to a search engine and it finds every docs site.
- `robots.txt`, which points at the sitemap.
- Every other file in [`static/`](static/), copied as-is: the stylesheet and the IndexNow key file that lets search engines accept change notifications for this host.

The page loads the Google Analytics tag for property `G-TGC4XRBK01`, which also verifies the site in Search Console.

A blog feed that cannot be read only drops the blog section. A GitHub API failure fails the build, and the live site stays as it was.

## When it runs

[`.github/workflows/pages.yml`](.github/workflows/pages.yml) tests, builds and deploys to GitHub Pages on every push to `master`, four times a day (half an hour after the profile README job), and on demand from the Actions tab.

## Local build

```bash
git clone https://github.com/psyb0t/psyb0t.git ../psyb0t
make build     # uses GITHUB_TOKEN, or gh auth token when gh is logged in
make test
make preview   # http://localhost:8000
```

## License

[WTFPL](LICENSE)
