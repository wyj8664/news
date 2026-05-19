# Cloudflare deployment

This project uses two Cloudflare layers:

1. Cloudflare Pages serves the generated static site from `output/`.
2. Cloudflare Workers + KV can serve high-frequency JSON such as `/markets/quotes.json`.

## 1. Pages

Create a Pages project named `newshot`, then add these GitHub secrets:

- `CLOUDFLARE_ACCOUNT_ID`
- `CLOUDFLARE_API_TOKEN`

The workflow at `.github/workflows/cloudflare-pages.yml` generates the site and runs:

```bash
npx wrangler pages deploy output --project-name newshot
```

The workflow is scheduled every 2 hours by default. The live market data can refresh faster through the Worker below, so Pages does not need a 10-minute redeploy loop.

## 2. Worker + KV for 10-minute data

Create a KV namespace:

```bash
cd cloudflare/worker
npx wrangler kv namespace create NEWSHOT_KV
```

Copy the returned namespace id into `wrangler.toml`:

```bash
cp wrangler.toml.example wrangler.toml
```

Deploy:

```bash
npx wrangler deploy
```

Then add a route for your production domain, for example:

```toml
routes = [
  { pattern = "news.example.com/markets/quotes.json", custom_domain = false }
]
```

With that route, the static page keeps loading `/markets/quotes.json`, but Cloudflare serves it from the Worker/KV value refreshed by cron every 10 minutes.

## Notes

- The Worker currently refreshes lightweight market data. Full news clustering still runs through the Python generator and Pages workflow.
- Keep Pages deployment at hourly or multi-hour frequency to avoid using Cloudflare Pages as a high-frequency job runner.
- If a quote source fails, the Worker keeps serving the last known value from KV and marks the payload with errors.
