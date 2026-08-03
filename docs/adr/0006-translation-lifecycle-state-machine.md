# Translation lifecycle state machine owns task and chunk state

The translation pipeline will use an explicit lifecycle state machine as the canonical source of truth for both Translation Task State and Translation Chunk State. We chose this over extracting more helper functions from `TranslationService` because the current problem is not just file size: task status, chunk status, progress events, and report stage all describe lifecycle in different vocabularies, which lets orchestration logic keep spreading through the service.

The state machine is a pure rule layer. It defines task states, chunk states, lifecycle events, and legal transitions; it does not write SQLite, emit progress events, or mutate reports. Side effects are handled by separate projectors: `TaskLifecycleProjector` projects task state into the task store, report, and task-level progress; `ChunkLifecycleProjector` projects chunk state into chunk records, report counters, and chunk progress.

Task states are:

- `created`
- `preparing_input`
- `preparing_translation`
- `processing_chunks`
- `finalizing_output`
- `completed`
- `completed_with_warnings`
- `failed`

Chunk states are:

- `queued`
- `translating`
- `checking_quality`
- `repairing`
- `reviewing`
- `accepted`
- `accepted_with_warnings`
- `failed`

Lifecycle events drive state transitions and are deliberately separate from progress events. A lifecycle event is the state machine input; a progress event is only a user-facing activity record. Task lifecycle events model both phase start and phase completion where that affects recovery or final status, while chunk lifecycle events model facts that change chunk state, such as translation starting, quality passing, repair being required, review being required, acceptance, warning acceptance, and failure.

The first implementation will cover normal chunk translation and semantic chunk translation together, including semantic timed-cue output. Keeping one path on the old scattered status strings while the other uses the state machine would preserve the split lifecycle vocabulary that this ADR is meant to remove.

The local SQLite task database is still an active-development artifact. When the store sees an older schema version, it may rebuild the task tables instead of migrating legacy status strings. This keeps the lifecycle vocabulary canonical from the first checked-in state-machine implementation and avoids carrying compatibility mappings for records that no user depends on.

We rejected a weak enum-only approach because it would still allow arbitrary status assignment across the pipeline. We also rejected an event-sourced design because it is heavier than the current recovery needs: the project needs guarded lifecycle transitions and clear projection, not a durable event log.

`completed_with_warnings` and `accepted_with_warnings` are intentional states. A translation task can successfully write a final subtitle while still containing failed, risky, or warning-accepted chunks; representing that as plain `completed` hides quality risk, while representing it as plain `failed` hides the fact that a usable artifact was produced.
