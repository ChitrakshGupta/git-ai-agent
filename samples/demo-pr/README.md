# Demo PR with planted issues

`cart.py` deliberately contains several issues across the four categories. The file itself has
no hints, so the agent has to find them from the code.

| Category | Planted issue |
|---|---|
| bug | `subtotal()` loops to `len(items) - 1`, so the last item is never added |
| bug | `apply_discount()` with an unknown code: `DISCOUNTS.get()` returns `None`, so `amount * None` raises `TypeError` |
| risk | `save_order()` builds SQL with an f-string, which allows SQL injection |
| risk | hard-coded live-looking API key `PAYMENT_API_KEY` |
| risk | the database connection in `save_order()` is never closed |
| missing_test | new pricing, discount and persistence logic, and no tests in the PR |
| style | unused `import os`; unclear variable name `x` |

**How to use it:** on a connected repo, open a PR that adds `cart.py`. For example, on GitHub
click **Add file → Create new file**, paste the contents, choose **Create a new branch** and
open the PR. The webhook triggers the agent and the review is posted on the PR.

`cart_base.py` is an optional clean base version. Commit it as `cart.py` on `main` first if you
want the PR to show a modification instead of a new file.
