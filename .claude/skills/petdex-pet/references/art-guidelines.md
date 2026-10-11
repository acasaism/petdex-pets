# Art guidelines

Rules learned while drawing the Courage pets. Each one fixed a defect the user actually spotted.

## Likeness

- Build one canonical model from the references (Courage: 3/4 view facing left) and use it in every row; mirror it for right-facing rows. A separately invented front view drifts off-model.
- Each round, render the reference next to frame 0 (`vs-reference.png`) and check the feature list one item at a time: silhouette, ears, nose/snout, eyes, mouth, limbs, hands/feet, tail, markings, palette.
- Verify iconic expressions and colours against references before "correcting" them. Courage's scream has pale-yellow teeth, a brown wavy tongue with an orange outline and an orange "U" uvula; recolouring them pink/white made it less faithful.
- Props come from the character's world (Courage types on a beige CRT, not a modern laptop).

## Readability on dark backgrounds

- Orca and many desktops are dark. Add a light outer rim (#FFF7F2, about 1.5–2 px in cartoon, 1 logical px in pixel art) behind the silhouette and props.
- Draw effect marks (speed lines, tremble arcs, tick bursts) in mid-light grey (#C9C9D2), with no outline, and leave them out of the rim; outlined and rimmed marks read as heavy white combs on dark backgrounds.
- Check a few cells on #F5F5F5 and #1E202C every round.

## Line work

- Cartoon (pycairo): thick dark outline, flat fills. Read cairo ARGB32 with `Image.frombuffer("RGBA", size, data, "raw", "BGRa", stride, 1)` to un-premultiply, or edges get dark fringes.
- Pixel art: selective outlining. Black only on the outer silhouette and true dark features (pupils, nose, spots, ear flaps). Interior detail (mouth, arm edges, creases) uses a darker fur shade. Long black lines across the torso or face read as cuts or scratches.
- Keep markings (spots) inside the silhouette so the outline stays continuous.

## Shading (pixel art)

- One light source (top-left) for every row and frame.
- Three steps per material: base, shadow, highlight. Hue-shift them: shadows toward purple/blue and a bit more saturated, highlights toward warm/yellow and lighter. Never pure black shadows or pure white highlights (eye whites are a feature, not a highlight).
- Shade by form, on the side away from the light. Avoid pillow shading (a uniform dark band along every edge).
- Cartoon variants stay flat, like the show.

## Antialiasing (pixel art)

- Sprites of 64+ logical px may use selective, manual 1-px antialiasing on interior curves (eyes, head contour against the face) with one intermediate colour.
- Never antialias the outer silhouette (the light rim needs a crisp edge) or tiny details.

## Hands and limbs

- Hands are a character feature: compact cross/finger shapes with a black outline, legible at 1x. Every visible arm ends in a visible hand.
- Pixel art: arms are thin strokes in the darker fur shade; raised or swinging arms start at the silhouette edge and must never cross the torso (thin lines over a same-colour body read as scratches).
- Cartoon: an outlined arm may pass in front of the body when it clearly reads as a limb ending in a hand.
- Clasped, typing and gripping hands must still read as hands (check at 3–4x), not blobs or tangles.

## Draw order and props

- Draw foreground elements (tongue, held props) last, with only their own outline; nothing may cross them.
- A magnifier lens must fully enclose the eye it magnifies; nothing of that eye may show outside the ring.
- Keep props readable: the character must not cover the part of a prop that tells the story (screen, keys under the hands, lens).

## Mouths and teeth

- Keep mouths on the jaw line under the snout, never floating on the cheek (a red block there reads as a wound).
- One mouth only: teeth or a grimace sit at the mouth corner, connected to the snout by a legible jaw line (in pixel art the mouth line may use a near-outline shade, as a true feature line). Teeth near the ear or the back of the head read as a second mouth, especially in mirrored rows.
- Gritted teeth are a short, wide grimace with one divider and a few tooth lines; a long band with many dividers reads as a zipper.

## Animation craft

- Clients hold some frames longer: Codex/Orca idle holds frames 0 and 5 (1680/1920 ms), Petdex Desktop holds them too (280/320 ms vs 110–140). Put key poses on held frames.
- Loops must be seamless: the last frame leads into the first.
- In looping states every frame differs from its neighbour (the validator reports duplicates).
- Markings move with the body part they sit on: if the torso shifts or leans, its spots shift with it (or keep the whole torso still). Spots fixed while the outline moves look painted on the screen.
- Run cycles alternate contact and passing poses with a 1-px body bob; jumps go crouch, ascend, hold at the peak, descend, land.
- Shading, antialiasing and animation-craft ideas adapted from willibrandon/pixel-plugin (MIT).

## Animation rows

| Row | Meaning |
|---|---|
| idle | Ambient loop: breathing, glance, blink |
| running-right / running-left | Locomotion; arms swing with hands outside the body |
| waving | Greeting with an open hand |
| jumping | Fright hop: crouch, up, peak, down, land |
| failed | The character's iconic panic (error state) |
| waiting | Asking the user for input; distinct from idle and review |
| running | A WORK loop (typing, busy), not literal running |
| review | Inspecting (e.g. magnifier) |

- Avoid identical consecutive frames unless intended: previews merge them and the motion looks thinner. `tools/validate_pet.py` lists them as INFO.
- No pixel of a used cell may touch the cell border (the validator checks it).
