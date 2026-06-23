# TUI repair retranslation uses timed cue output

When a user triggers repair retranslation during review, the model output should target the timed cues the user is reviewing, even when the model is given full semantic units as context. We chose this over returning semantic-unit JSON and splitting it back again because the review problem is visible at timed-cue level: users expect the selected retranslation range to be repaired directly, while structural changes remain the responsibility of layout repair or explicit user merges.
