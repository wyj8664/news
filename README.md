# NEWSHOT

NEWSHOT is a customized TrendRadar news site. It generates curated news pages, daily reports, event timelines, source hotlists, and a small market watch panel.

## Local Run

```bash
docker compose up -d
```

Open:

```text
http://localhost:8080/
```

## Cloudflare Deploy

The repository includes:

- `.github/workflows/cloudflare-pages.yml` for Cloudflare Pages deployment
- `cloudflare/pages/_headers` for cache headers
- `cloudflare/worker/` for the optional Worker + KV live data layer

See `cloudflare/README.md` for setup steps.
