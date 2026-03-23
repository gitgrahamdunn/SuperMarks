# Experimentation Reference

This note captures the preferred A/B testing architecture for SuperMarks when experimentation becomes a priority later.

## Summary

SuperMarks should use a backend-owned experiment system with teacher-level assignment and server-side event logging.

The frontend should receive only a small `experiment_flags` payload and render the assigned variant as normal product UI, with no visible test-mode noise.

Chosen defaults:

- assignment unit: `teacher`
- primary success metric: `workflow time`

## Core rules

- Keep experiment assignment on the backend, not in the browser.
- Persist assignments so each teacher stays on one variant across visits.
- Use the existing signed session or cookie identity as the temporary subject key until fuller teacher auth exists.
- Expose a compact `experiment_flags` object through normal API/bootstrap payloads.
- Do not use query params, localStorage flags, banners, badges, or debug UI to control production experiments.
- Log exposure and workflow events in the backend so analysis reflects the real workflow, not just client-side impressions.

## Recommended data model

### Experiment registry

- experiment key
- active/inactive
- variants
- rollout weights
- assignment unit

### Experiment assignment

- subject key
- experiment key
- assigned variant
- created_at

### Experiment event

- timestamp
- subject key
- experiment key
- variant
- event name
- optional exam/submission ids
- metadata JSON

## First event set

- `experiment_exposed`
- `intake_started`
- `initial_review_ready`
- `workspace_opened`
- `front_page_queue_opened`
- `paper_confirmed`
- `front_page_queue_completed`
- `export_started`

Primary metric:

- elapsed time from `intake_started` to `front_page_queue_completed` or `export_started`, depending on the tested flow

Secondary metrics:

- error rate
- completion rate
- retries
- stalls

## Frontend rules

- The frontend should consume assigned variants as ordinary config.
- Exposure should only be logged when the tested surface is actually rendered.
- Control must be an explicit variant.
- If assignment lookup fails, fall back to control silently.
- No teacher-facing UI should indicate that an experiment is running.

## Safety and rollout

- sticky assignment per teacher
- kill switch per experiment
- no frontend randomization
- no vendor analytics SDK required for v1
- results can be analyzed from SQL, CSV export, or a simple backend report endpoint later

## Implementation notes for later

- Best place to inject `experiment_flags` is the existing normal exam/bootstrap responses.
- Best first tested surfaces are Home intake and the front-page review flow, because they already have clear workflow timing and completion events.
- Keep the first implementation intentionally quiet and operational, not productized as a user-facing experimentation platform.
