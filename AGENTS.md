# Agent guide

Repo of Codex-format desktop pets (Orca, Codex, Petdex Desktop). See README.md for the catalog.

## Skills

| Skill | Path | Use when |
|---|---|---|
| petdex-pet | `.claude/skills/petdex-pet/SKILL.md` | Creating, updating, validating or publishing any pet |

Read the skill before touching anything under `pets/`.

## Commands

- `/new-pet <character> [from <show>] [pixel|cartoon|both]` (`.claude/commands/new-pet.md`) — starts a new pet: gathers references and requirements, then follows the skill.

## Tools

- `python3 -I tools/validate_pet.py [--compare OLD_SHEET] pets/<character>/<variant>` — format and content checks; must exit 0 before a commit.
- `python3 -I tools/make_previews.py pets/<character>/<variant>` — refresh `preview/` after any spritesheet change.
- `python3 -I tools/review_sheets.py [--baseline OLD_SHEET] [--refs DIR] pets/<character>/<variant>` — review sheets + `checklist.md` for every visual change.

## Conventions

- One folder per variant: `pets/<character>/<variant>/` with `pet.json` (id = Petdex slug), `spritesheet.webp`, `make_pet.py`, `preview/`.
- Tags: `<character>-<variant>-vN`. Conventional Commits.
- Reference art and generator outputs stay out of git.
