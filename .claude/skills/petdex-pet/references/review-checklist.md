# Review procedure

Every escaped defect so far had one of these causes: single frames reviewed instead of motion, a few
"representative" frames instead of all, light background only, no reference comparison, or trusting a
subagent's self-QA. This procedure removes the choice of what to look at.

## Generate

```sh
python3 -I tools/review_sheets.py \
  --baseline <(git show <prev-tag>:pets/<character>/<variant>/spritesheet.webp) \
  --refs pets/<character>/refs \
  pets/<character>/<variant>
```

Omit `--baseline` for a new pet. Reference art lives in the git-ignored `pets/<character>/refs/` (character art at the top level, props in `refs/props/`, origins in `SOURCES.txt`); the tool reads up to 6 top-level `.png/.jpg/.jpeg/.webp` files, decoding PNG, JPEG or WebP whatever that extension claims. Output goes to the
git-ignored `pets/<character>/<variant>/review/`, including `checklist.md`.

## Pass 1: overview

LOOK at `overview-light.png` and `overview-dark.png`: silhouette, rim, effect marks, consistency between rows.

## Pass 2: motion and detail, every row

LOOK at every `motion-<row>-<state>.png` and fill the `checklist.md` table, one row per state:

| Column | What to check |
|---|---|
| likeness | Matches the references and the canonical model (`vs-reference.png`) |
| hands/limbs | Every visible arm ends in a legible hand; pixel art: no line crosses the torso |
| mouth/teeth | ONE mouth: on the jaw line and visibly connected to the snout (check mirrored rows too); teeth read as teeth at 1x |
| eyes | Inside props that cover them (lens); pupils readable |
| props | Readable; the character does not cover the part that tells the story |
| markings & motion | Red diff band and onion skin: parts that move together do move together (spots with torso) |
| dark background | Readable on `overview-dark.png` |
| loop seam | Last frame flows into frame 0 without a jump |

Write `OK` or the concrete defect in each cell. A review without the filled table is not done.

For updates, also LOOK at `compare-baseline.png`: only the intended rows changed, and they changed as intended.

## Then

- Report the filled table, then fix and regenerate. About 4 rounds at most.
- When a subagent drew the pet, the parent runs this procedure itself.
- Before publishing, the user watches `preview/*.gif` and gives an explicit go.
