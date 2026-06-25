# Desktop work progress is the primary area; task config is a right-side drawer

In the Electron desktop UI, the work-progress / result panel is the primary area and stays visible; the per-task configuration form (translate / download / transcribe / settings) is a right-side drawer that slides in when a task tab is selected and collapses to a thin tab when dismissed or while a job is running. We chose this over the previous layout — a form panel stacked above a progress panel that fought each other for vertical space — because a translation task spends most of its wall-clock time running, not configuring, and during a run the only thing the user needs to see is what the program is doing.

## Why the previous layout felt like a tool

The old `workspace` stacked the active `task-panel` (the configuration form, 12+ fields) on top of the `run-panel` (progress, chunk activity, results, logs). The progress panel was `flex: 1`, so it permanently reserved a large block at the bottom even when idle, and the moment a form got long it compressed the progress area right when the user needed it most. The two panels had mutually exclusive ideal sizes: configuration wants vertical room while the user fills it in, progress wants vertical room while the job runs. Sharing one scroll column meant one was always starved.

## The choice and the trade-off

The drawer is fixed-width (~420px) and overlays the right edge of the primary area, so the progress area always keeps a usable width for the chunk activity grid even while the drawer is open. Drawer state (`configDrawerOpen`) lives in `useAppController`; `isBusy` auto-collapses it so a running job reclaims the full primary area. The thin tab left behind when collapsed preserves the "I'm in the translate task" context and is one click to reopen. All existing `id` / `data-*` selectors are kept so e2e and smoke tests are unaffected.

Trade-off accepted: while the drawer is open the primary progress area is narrower, and on a very narrow window a fixed-width drawer competes with the left sidebar for room. We accept this because the drawer is dismissed the moment a job starts (auto-collapse on `isBusy`), so the narrow-primary state is short-lived, and the responsive breakpoint degrades the drawer gracefully. The rejected alternatives were a full-screen modal (loses progress visibility entirely, awkward to re-open mid-run) and an inline collapsible card above the progress area (still leaves configuration competing with progress in the same column — the original problem).

This aligns with `docs/prd-desktop-work-progress-visibility.md` (stories 12 and 27): the desktop surface exists to show what the program is doing during a run, and configuration is a means to that end, not a peer surface.
