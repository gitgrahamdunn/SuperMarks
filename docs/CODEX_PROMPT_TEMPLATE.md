# Codex Prompt Template

All future Codex prompts MUST begin with:

"Follow Strategy A API routing. Do not introduce rewrites or alternate routing mechanisms."

They should also include:

"Before making any non-trivial change, restate the product-direction rule that applies to this workflow and shape the fix around it."

For the current SuperMarks product direction, include these operating assumptions when relevant:

- multiple uploaded front-page images are separate student papers by default
- once a test is no longer `Preparing`, the review flow should already be ready to open
- background preparation should not block the teacher in a modal or wizard
- prefer the simplest teacher-first workflow over technically clever but more confusing behavior
