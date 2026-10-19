# Contributing

Issues and pull requests are welcome: fixes to a sprite, new pets or variants, tooling improvements.

## Steps

1. Install the dependencies: `pip install -r requirements.txt` (`pycairo` needs the Cairo system library and headers).
2. Edit the variant's `make_pet.py` and regenerate it (see [Regenerate](#regenerate)); copy the new `spritesheet.webp` over the committed one.
3. Validate: `python3 -I tools/validate_pet.py pets/<character>/<variant>` must exit 0.
4. Refresh the previews: `python3 -I tools/make_previews.py pets/<character>/<variant>`.
5. Open a pull request with the preview GIFs of what changed, using Conventional Commits.

Reference art is not committed, so mention in the PR where yours comes from.

## New pets

Creating or updating a pet follows the `petdex-pet` skill (`.claude/skills/petdex-pet/`), which Claude Code loads
automatically in this repo; other agents start from `AGENTS.md`. In short: design from reference images, two review
passes (overview and zoomed detail), `tools/validate_pet.py` must pass, refresh previews, commit and tag.

To start a new pet with Claude Code, run `/new-pet <character> [from <show>] [pixel|cartoon|both]`, attaching reference images if you have them.

## Tags

`<character>-<variant>-vN`, e.g. `courage-cartoon-v4`. Tags are repo-wide, so the character name keeps them unambiguous.

## Layout

```
pets/<character>/<variant>/
├── pet.json          # published metadata (id = Petdex slug)
├── spritesheet.webp  # 1536x1872 lossless RGBA WebP, 8 columns x 9 rows of 192x208 cells
├── preview/         # GIF per state + sheet.png, rendered by tools/make_previews.py
└── make_pet.py       # generator
```

Rows, in order: idle (6), running-right (8), running-left (8), waving (4), jumping (5),
failed (8), waiting (6), running (6, the "working" state), review (6).

## Regenerate

```sh
python3 -I pets/courage/pixel/make_pet.py     # writes pets/courage/pixel/build/
python3 -I pets/courage/cartoon/make_pet.py   # writes pets/courage/cartoon/out/
```

Generators write a local build (with their original local ids `agallas` / `agallas-cartoon`) plus QA images,
all git-ignored. Copy the new `spritesheet.webp` over the committed one to update a pet.
Reference art is not committed: keep it in `pets/<character>/refs/` (git-ignored, with `SOURCES.txt`) and compare with `tools/review_sheets.py --refs pets/<character>/refs`. The Courage generators' own comparison images expect file names from their original session and are skipped when those files are missing.

After committing a new spritesheet, refresh its previews:

```sh
python3 -I tools/make_previews.py pets/<character>/<variant>
```
