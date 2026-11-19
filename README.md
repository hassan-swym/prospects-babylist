# prospects-babylist

Registry-fit signals for Babylist brands that run on Shopify, read from public storefront endpoints (`/products.json` and the homepage).

## Files

- `babylist-shopify-stores.csv`: input, 211 deduplicated Shopify stores.
- `babylist-store-signals.csv`: output, one row per store.
- `babylist-store-signals-skipped.txt`: stores skipped because Shopify rate-limited the request. Rerun the workflow to retry them.
- `score_stores.py`: the scoring script. Skips stores already in the output.
- `logs/`: per-job run logs from the last workflow run.
- `.github/workflows/score.yml`: runs the script in parallel jobs on GitHub Actions, then merges and commits results.

## Running

Actions tab, "Score stores", Run workflow. Inputs: `shards` (parallel jobs) and `limit` (max stores per job, 0 for all).

## Caveats

- Prices are requested in USD (`country=US`).
- Fisher-Price resolves to shop.mattel.com, so its row covers the whole Mattel store.
- Existing-app detection (Swym, wishlist and registry apps) has not been validated against a store known to run one. Treat that column as provisional.
- Category counts come from keyword matching on product type, title and tags. Check `pct_products_unclassified` and `top_product_types`.
