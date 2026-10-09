# agallas-pets

Fan-made Codex/Petdex desktop pets of Agallas (Courage the Cowardly Dog), for personal use.
Courage is a Cartoon Network character; this repo claims no rights to the character.

| Folder | Style |
|---|---|
| `agallas/` | Pixel art |
| `agallas-cartoon/` | Cartoon (cairo vector) |

Each folder holds `pet.json` + `spritesheet.webp` (1536x1872, 8x9 cells of 192x208) and the
`make_pet.py` generator when available. Versions are git tags (`pixel-vN`, `cartoon-vN`).

Install: copy a folder to `~/.codex/pets/<id>/` (Orca: Import .codex-pet bundle) and `~/.petdex/pets/<id>/`.

## Published on Petdex

Submitted 2026-10-09 by `acasaism` with license `cc-by-nc` (held for review). Track at https://petdex.dev/u/acasaism.

| Slug | Source |
|---|---|
| `courage-the-cowardly-dog` | `submission/courage-the-cowardly-dog/` (cartoon) |
| `courage-the-cowardly-dog-pixel` | `submission/courage-the-cowardly-dog-pixel/` (pixel) |

Update a published pet: `npx petdex edit <slug> --sprite <path>/spritesheet.webp` (also `--desc`, `--displayName`).
