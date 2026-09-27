#!/bin/sh
# Data for the README and site pictures: seed/example.sh plus some activity.
# Run on an empty database:  RIVER_DB=/tmp/shots.db seed/screenshots.sh
set -e
D="$(dirname "$0")"
R="$D/../bin/river -q"
"$D/example.sh" >/dev/null

$R register api-agent --note "backend work" >/dev/null
$R --as alex project describe website "Storefront pages in web/; React. Checkout, pricing, footer." >/dev/null
$R --as alex project describe backend "Orders service in api/; Python. Owns the orders API." >/dev/null
$R --as alex edit 5 --context "Footer links to /help and /terms return 404 since the docs moved to /docs/." \
  --touches web/components/Footer.tsx --check "npm test -- Footer" >/dev/null

$R --as alex add backend "Add order status webhooks" --doer ai --touches api/webhooks.py --after 2 >/dev/null  # 6
$R --as alex add website "Order history page" --doer ai --touches web/orders/ --after 2 >/dev/null              # 7
$R --as alex add website "Accessibility pass on checkout" -p 2 --doer ai --after 4 >/dev/null                   # 8

$R --as api-agent claim 1 >/dev/null
$R --as api-agent done 1 --output "api/orders.md: POST /orders, GET /orders/{id}, status enum" >/dev/null
$R --as api-agent claim 2 >/dev/null
$R --as alex send question "Is the orders API paginated? The order history page needs it." --item 2 >/dev/null
echo "Loaded. Try: river serve --open, river status, river go --project website"
