# SQLite owns translation task records

Translation task records and resume state will move from sidecar checkpoint JSON files into a local SQLite database stored in the user's application data directory, and new versions will not preserve compatibility with existing `*_checkpoint.json` files. We accept this because the project is still in active development, so keeping a JSON-to-SQLite migration path would add more complexity than value; subtitle, media, trace, and log artifacts remain files referenced by the translation task record rather than being moved into the database.

Translation task records use soft deletion: deleting a record hides it by marking `deleted_at`, while referenced subtitle, media, trace, log, and context files are left untouched. The first SQLite version will not provide permanent record deletion or automatic artifact cleanup.

The first SQLite scope is `translate` only. A translation task record stores the non-sensitive configuration snapshot used when the task was created, so resume continues the same task semantics even if the user's current model or pipeline settings have changed. API keys remain outside SQLite and are resolved from the existing environment/settings path at runtime.

Resume state is stored at two grains: translated timed-cue state for the user-visible subtitle result, and translation-chunk progress for pipeline recovery and progress display. Task, cue, and chunk state use relational tables; flexible data such as configuration snapshots and quality-diagnosis summaries may be stored as JSON fields. LLM trace prompt/response files remain file artifacts, with SQLite storing only trace directory and trace identifiers or paths.

The CLI keeps `--resume` and adds `--task-id` for exact recovery. A task-id resume uses the stored record and must not accept new input or output overrides; path-based resume may still locate a recoverable record by normalized input fingerprint, target language, output format, and output file. Desktop history uses task IDs directly.

Schema evolution is handled by a lightweight in-process migration runner using SQLite `PRAGMA user_version`, not an external migration framework. The store is exposed to the pipeline as `TranslationTaskStore`, so translation code depends on task-record operations rather than raw SQL.
