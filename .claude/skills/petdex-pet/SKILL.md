---
name: petdex-pet
description: "Trigger: new pet, create character, update pet, fix sprite, spritesheet, codex pet, petdex publish. Create, update and publish desktop pets in this repo."
license: Apache-2.0
metadata:
  author: "acasaism"
  version: "1.0"
---

## Activation Contract

Load before creating a character or variant under `pets/`, changing any `spritesheet.webp` or `make_pet.py`, or publishing/updating a pet on Petdex.

## Hard Rules

- Design from references: gather 3+ reference images (canonical look plus the poses you need), LOOK at them, and list the character's features before drawing. Never invent the design. Keep reference art out of git (`PET_REFS_DIR`).
- Apply every rule in `references/art-guidelines.md`.
- Before reporting any visual change, run `tools/review_sheets.py`, LOOK at every image it writes and fill its `checklist.md` per `references/review-checklist.md`. The parent does this even when a subagent drew; subagent self-QA is not enough.
- Diff images on all channels (`getbbox(alpha_only=False)`); alpha-only diffs miss colour changes.
- Every accepted version gets a commit and a tag `<character>-<variant>-vN`; `tools/validate_pet.py` must exit 0 and previews must be refreshed first.
- Publishing, withdrawing and licence choice are the user's decisions. Fan art never uses `cc0`, `cc-by` or `cc-by-sa`.

## Decision Gates

| Situation | Path |
|---|---|
| New character or variant | Create steps |
| Redraw or fix an existing pet | Update steps |
| Petdex status `pending` | User withdraws on the web ("Retirar envío"), then `submit` |
| Petdex status approved | `npx petdex edit <slug> --sprite <sheet>` |
| Several pets to submit | One `submit` per folder |

## Execution Steps

Create:
1. Gather references; write a feature checklist from them.
2. Create `pets/<character>/<variant>/` with `pet.json` (`id` = Petdex slug) and `make_pet.py`, starting from an existing generator. Model one canonical view and mirror it for the other direction.
3. Generate (output goes to a git-ignored `build/` or `out/`); copy the new `spritesheet.webp` into the variant folder, keeping its `pet.json`. Render the reference next to frame 0 and fix feature by feature.
4. Review per `references/review-checklist.md`, fix, repeat (about 4 rounds max).
5. `python3 -I tools/make_previews.py pets/<character>/<variant>`, then `python3 -I tools/validate_pet.py pets/<character>/<variant>` → exit 0 (it fails on stale previews).
6. Copy the folder to `~/.codex/pets/<id>/` and `~/.petdex/pets/<id>/` to test; tell the user to re-import it in Orca.
7. Commit, tag, add the README catalog row.

Update:
1. Change only the targeted rows in `make_pet.py`; regenerate and copy the spritesheet in as in Create step 3.
2. `python3 -I tools/validate_pet.py --compare <(git show <prev-tag>:pets/<character>/<variant>/spritesheet.webp) pets/<character>/<variant>` must list only the intended rows.
3. Review per `references/review-checklist.md` (with `--baseline`).
4. Refresh previews, validate, commit, tag the next version, update the README status.
5. Before publishing, point the user to the refreshed `preview/*.gif` and wait for an explicit go; then publish per the Decision Gates.

## Output Contract

Return: files and tag changed; the filled checklist table; validator result, including INFO lines (duplicate frames, changed rows from `--compare`); user actions still needed (Orca re-import, Petdex withdraw); pending items.

## References

- `references/art-guidelines.md` — visual and animation rules.
- `references/format-and-publishing.md` — format, install, Petdex CLI, licences.
- `references/review-checklist.md` — review procedure and checklist columns.
- `tools/validate_pet.py`, `tools/make_previews.py`, `tools/review_sheets.py` — repo tools (paths from the repo root).
- `pets/courage/` — example: `cartoon` (pycairo), `pixel` (Pillow).
