# Agent guide

Repo of Codex-format desktop pets (Orca, Codex, Petdex Desktop). See README.md for the catalog.

## Skills

| Skill | Path | Use when |
|---|---|---|
| petdex-pet | `.claude/skills/petdex-pet/SKILL.md` | Creating, updating, validating or publishing any pet |

Read the skill before touching anything under `pets/`.

## Tools

- `python3 -I tools/validate_pet.py [--compare OLD_SHEET] pets/<character>/<variant>` — format and content checks; must exit 0 before a commit.
- `python3 -I tools/make_previews.py pets/<character>/<variant>` — refresh `preview/` after any spritesheet change.

## Conventions

- One folder per variant: `pets/<character>/<variant>/` with `pet.json` (id = Petdex slug), `spritesheet.webp`, `make_pet.py`, `preview/`.
- Tags: `<character>-<variant>-vN`. Conventional Commits.
- Reference art and generator outputs stay out of git.
