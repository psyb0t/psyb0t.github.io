# psyb0t.github.io

The code behind [psyb0t.github.io](https://psyb0t.github.io/): a bit about me, my most starred shit, my latest blog posts, and every public project I have, sorted by category.

I don't maintain the project list by hand. The build pulls it from the GitHub API with the same [`update.py`](https://github.com/psyb0t/psyb0t/blob/master/scripts/update.py) that regenerates my [profile README](https://github.com/psyb0t/psyb0t), and sorts it with the same [`categories.json`](https://github.com/psyb0t/psyb0t/blob/master/categories.json). Want a repo in another category? Edit `categories.json` over there. This site catches up on its next build.

A GitHub Action rebuilds and deploys it on every push, four times a day, and whenever I hit the button. If the blog feed is down, the blog section just disappears. If the GitHub API is down, the build fails and the live site stays as it was.

Each category also gets its own page under `/projects/<slug>/`, with a title, description and intro from [`pages.json`](pages.json), and `/projects/` lists them all. Every project sits on exactly one of those pages. The home page shows the first five of each category and links to the rest. A category with no entry in `pages.json` gets no page, and the build logs a warning instead of failing.

Besides the pages, the build writes a `sitemap.xml` that points at these pages and at the sitemap of every project docs site under psyb0t.github.io, so one sitemap submission covers all of them. It also writes an `llms.txt` that lists every project with its docs site and that site's own `llms.txt`, for LLMs and agents. And it copies whatever is in [`static/`](static/), which is the stylesheet, the shared analytics script and the IndexNow key. Every page carries the Google Analytics tag, which is also how Search Console verifies the site.

## Build it yourself

```bash
git clone https://github.com/psyb0t/psyb0t.git ../psyb0t
make build     # takes GITHUB_TOKEN, or gh auth token if gh is logged in
make test
make preview   # http://localhost:8000
```

## License

[WTFPL](LICENSE)
