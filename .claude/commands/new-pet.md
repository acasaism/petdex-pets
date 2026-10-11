---
description: Start a new desktop pet (character) with the petdex-pet skill
argument-hint: <character> [from <show>] [pixel|cartoon|both]
---

Create a new pet for: $ARGUMENTS

1. Load `.claude/skills/petdex-pet/SKILL.md` and its references; follow its Create steps and Hard Rules.
2. Collect what only the user knows before drawing. Ask one question at a time, and only for what the arguments and any attached images do not already answer:
   - character and source (show, film, game);
   - variants: pixel, cartoon or both;
   - reference images: use the ones attached to this conversation; otherwise propose a source (for example the show's wiki) and download into the git-ignored `pets/<character>/refs/` (props in `refs/props/`, origins in `SOURCES.txt`);
   - the character's signature look per state where it matters: `failed` (panic), `running` (how they work), `review` (inspecting), `waiting` (asking);
   - whether it will be published: display name and licence (fan art: `cc-by-nc` or `all-rights-reserved`).
3. Create a branch and an ODD feature document with one task per variant. When several characters are requested, handle them one at a time and get approval for each before starting the next.
4. After the first draft of each variant, run `tools/review_sheets.py --refs pets/<character>/refs pets/<character>/<variant>`, fill the checklist, and show the user `vs-reference.png` and the preview GIFs; iterate on their feedback.
5. Never publish without the user's explicit go.
