#!/bin/sh
# Example: two projects with dependencies across them. Run on an empty database.
set -e
R="$(dirname "$0")/../bin/river -q --as alex"

$R register alex --human --note "owner"
$R project add website
$R project add backend

a() { $R add "$@" >/dev/null; }
a backend "Design the orders API" --doer ai --context "Write api/orders.md: endpoints, fields, status values"  # 1
a backend "Build the orders API" --doer ai --after 1                 # 2
a website "Write the pricing page copy" --doer human                 # 3
a website "Build the checkout page" -p 0 --doer ai --after 2 3       # 4
a website "Fix footer links" -p 3 --context "/help and /terms links 404 since docs moved to /docs/"  # 5
echo "Loaded. Try: river next, river blockers 4, river serve --open"
