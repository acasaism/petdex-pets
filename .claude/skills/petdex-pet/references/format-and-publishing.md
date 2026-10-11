# Format and publishing

## Atlas contract

Codex pet format, as shipped in Orca's `codex-pet-sprite-defaults.js`.

- `spritesheet.webp`: lossless WebP (`save(..., "WEBP", lossless=True)`), RGBA, 1536x1872 = 8 columns x 9 rows of 192x208 cells, transparent background, unused trailing cells fully transparent.
- Rows in order (frames, per-frame ms; last frame holds longer):

| Row | State | Frames | Timing |
|---|---|---|---|
| 0 | idle | 6 | 1680, 660, 660, 840, 840, 1920 |
| 1 | running-right | 8 | 120 … last 220 |
| 2 | running-left | 8 | 120 … last 220 |
| 3 | waving | 4 | 140 … last 280 |
| 4 | jumping | 5 | 140 … last 280 |
| 5 | failed | 8 | 140 … last 240 |
| 6 | waiting | 6 | 150 … last 260 |
| 7 | running | 6 | 120 … last 220 |
| 8 | review | 6 | 150 … last 280 |

- Petdex Desktop uses the same rows but plays idle faster: 280, 110, 110, 140, 140, 320 ms.
- `pet.json` has exactly `id`, `displayName`, `description`, `spritesheetPath`. Petdex trims `displayName` to 60 and `description` to 280 characters; the slug derives from `id`.
- Discoverability: use the character's canonical English name in `displayName` and put localized names in `description` (search matches slug, name and description).

## Local install and testing

- Codex and Orca read `~/.codex/pets/<id>/`; Petdex Desktop reads `~/.petdex/pets/<id>/`.
- Orca keeps its own copy after "Import .codex-pet bundle": the user must re-import after every change.
- The Courage pets are installed locally under the legacy ids `agallas` and `agallas-cartoon`, and their generators still build with those ids.

## Petdex CLI behaviour

- Login opens a browser: the user runs `npx petdex login`; never handle tokens.
- `npx petdex submit <folder> --license <id>` — one folder per call. A parent folder with several pets asks an interactive confirmation that a non-TTY run cannot answer (it exits without submitting).
- The CLI label is the folder name (e.g. `pixel`); the slug comes from `pet.json` `id`.
- Franchise characters come back "held for review: possible policy issue" (human moderation).
- Re-running `submit` is safe: an existing submission triggers an interactive duplicate prompt that a non-TTY run cannot answer, so nothing is sent. Never pass `--force` (it creates `-2` slugs).
- Pending submissions are not editable (`pet_not_editable`); only the web profile can withdraw them ("Retirar envío"). Approved pets update with `npx petdex edit <slug> --sprite <sheet>` (also `--desc`, `--displayName`).
- The spinner output is noisy: save raw output to a file, strip ANSI codes, and read the `✓ <label> → <slug> …` line.
- The CLI points to `https://petdex.dev/u/<username>` for status, but the user could not see the held submission there; treat the CLI's `✓ <label> → <slug>` line as the proof of submission.

## Licences

`cc0`, `cc-by` and `cc-by-sa` allow commercial use (Petdex `COMMERCIAL_PET_LICENSES`). For fan art of a character you do not own, offer `cc-by-nc` or `all-rights-reserved`; the user decides. No licence covers the character itself; rights holders can request a takedown.
