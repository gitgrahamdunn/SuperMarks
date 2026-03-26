# Production Guardrails

## Product check before implementation

Before any non-trivial change, explicitly restate the product-direction rule that governs the fix.

Use this order:
- product direction
- teacher workflow intent
- existing UX pattern
- technical fix

If a likely technical fix conflicts with the current product direction, stop and resolve that conflict before changing code.

Current workflow defaults that must be preserved:
- multiple uploaded front-page images default to one student paper per image
- a test that is no longer `Preparing` should open directly into a ready review flow
- background work should happen in the background; the teacher should not be trapped waiting in a modal
- mobile simplicity and low-clutter review take priority over debug-heavy or over-explained UI

## Before merging any PR that touches:
- `frontend/public/_redirects`
- `frontend/wrangler.toml`
- `api/*`
- routing
- CORS
- API base URL

You must:
- Confirm smoke tests pass
- Confirm no rewrites for `/api` exist
- Confirm only one API routing strategy is active
