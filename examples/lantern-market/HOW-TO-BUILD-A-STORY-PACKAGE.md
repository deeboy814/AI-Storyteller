# How to build a story package (with the example "The Lantern Market")

A **story package** is one `.zip` file that holds everything an author makes for a story: the prompt, the opening, the cast and their pictures, places, art settings, side-panel trackers, endings and a starter player character. Players import it from the Home screen with **📦 Import a story** and can start playing at once.

The example package **The Lantern Market** (`Example Story Package - The Lantern Market.zip`) is a complete, playable story built to show every part. Import it, open **📝 Edit story** and read the lines that start with `GUIDE:` in the prompt; then come back here for the details.

The quickest way to make your own: import the example, change each part in the app, then export it with **💾 Export & backups → Story package**.

---

## 1. The story prompt (📝 Edit story → Prompt)

**What it is:** the instructions the narrator reads before *every* reply. Everything that must always be true goes here.

**What to put in it**, in this order, each under a short heading:

| Section | What goes here | Example from The Lantern Market |
|---|---|---|
| Opening line | Who the narrator is and who the player is. Use `{user}` for the player's name. | "You are the narrator of an interactive mystery set in Vessaline… The player is {user}, a newly licensed Finder." |
| World | Concrete facts the narrator can reuse: places, technology, money, what is normal. One fact per bullet. | Fog every evening, gas lamps, no electricity, "a hot meal costs 1 coin". |
| The player | The player's role and starting situation. Point to the avatar sheet for stats. | "{user} has just received a Finder's licence… their lantern is unlit." |
| Main characters | For each: who they are, how they talk, what they want, one secret or tension. | Ottilie the Warden "calls {user} 'Finder' until she trusts them". |
| Species (optional) | One line per non-human people. | "Mothfolk: small, soft-furred… see well in the dark". |
| Rules | What the narrator must enforce, and the hidden truth with when it may be revealed. | "The truth (secret until discovered): a collector called the Hollow Patron…" |
| Writing style | Person and tense, reply length, tone, how dialogue looks. | "Second person, present tense… 250 to 450 words… cosy but uneasy." |
| Status line (optional) | One line of `Label: value` pairs printed at the end of every reply. | `[MARKET] Night: 1   Coins: 12   Lanterns: 3/10   Reputation: Newcomer   Level: 1   XP: 0/50` |
| Quests (optional) | When to give quests and the exact line format. | `[QUEST] Started: … Objectives: 1) … 2) … Reward: 40 XP, 5 coins.` |

**For best results**
- Write rules as short bullet points. The AI follows a clear list far better than long paragraphs.
- Say what is secret and when it may be revealed; the narrator needs to know the truth to plant fair clues.
- Give exact formats in backticks (status line, quest lines) and say when to print them ("at the end of every reply").
- Keep it between about 1,500 and 6,000 words. Very long prompts get skimmed.
- Do not describe characters' looks in the prompt: that belongs on their cast cards (section 5).
- The app adds its own instructions for pictures (portrait tags, place tags), so a one-line reminder is enough.

---

## 2. The prologue (📝 Edit story → Options)

**What it is:** a short scene-setter the player reads before the first message.
**Length:** 2 to 5 sentences. No choices, no dialogue.
**Example:** "Vessaline is a city of a hundred islands, fog and lamplight. On moonless nights the Lantern Market rises… Tonight, for the first time, the market's lanterns are going out."

## 3. The opening (📝 Edit story → Opening)

**What it is:** the narrator's first message, written by you. It sets the voice the AI will copy, so write it exactly the way you want every reply to look.
**Length:** 150 to 400 words.
**Include:** a place picture tag for the first location (`![](place:The Lantern Market)`), at least one character speaking with a portrait tag before their line (`[[Ottilie Brask/stern]]` then the quote), and end on a hook the player can answer.
**Tip:** keep "Send the opening to the AI" on, so the narrator sees it as its own first reply.

## 4. Suggested first moves and endings (📝 Edit story → Options)

- **First moves:** 2 to 4 different one-sentence moves (one cautious, one bold, one curious). They appear as buttons under the opening.
- **Endings:** 2 to 4, each with a title and 1 to 3 sentences of what has to happen. They are hidden behind the 🏁 button, and the player can choose one to steer toward. Mix a happy ending, a bittersweet one and a failure.

## 5. The cast (🎭 Cast → a character)

One card per main character. Each card has:

| Box | What to write | Length | Example (Ottilie Brask) |
|---|---|---|---|
| Name | The name the narrator uses in portrait tags. | | Ottilie Brask |
| Also called | Other names the story uses, separated by commas. | | the Warden, Warden Brask |
| Species | Empty for humans; otherwise the species name. | | (empty) |
| How they look | What the artist draws: age, build, hair, eyes, face, clothes, one distinctive detail. | 40 to 70 words | "A stern woman of about fifty… round brass spectacles… dark green oilcloth coat… a heavy ring of keys at her belt." |
| Names and forms of address | Who may call them what. | 1 to 2 sentences | "Most people call her Warden. Only old friends call her Ottilie." |
| Personality | How they talk. | one line | "Brisk, dry and fair; short sentences." |
| Bio | Who they are in the story and what they want. | 2 to 4 sentences | "The Market Warden for thirty years…" |
| Tell the narrator about them | Tick for the main cast, so their card is sent with the prompt. | | ticked |

**Pictures:** in the character's **Images** tab use **Draw options…** to pick a face (it becomes *Normal 1*, the reference for every later picture), then draw a set of moods. Describe looks the same way every time; the first picture decides the face for good.

## 6. Species (🖼 Art → Characters → Species & makers…)

For non-human peoples: a description of what is true of the whole species (body, face, covering, eyes, wings), and optionally a picture maker. The first picture of a species becomes its reference. **Be explicit about features a picture model might confuse**: the example says "NO animal ears, two long feathery antennae", because a moth person was first drawn with rabbit ears.

## 7. Places (🖼 Art → Places)

Locations the story returns to. Each has a **name** the narrator uses in place tags (`![](place:The Drowned Archive)`) and a **1 to 3 sentence visual description** (light, materials, scale, mood). Draw one picture per place. The example has three: a market, a flooded library and an office.

## 8. Art settings (🖼 Art → Style & options)

| Setting | What it does | Example |
|---|---|---|
| Art direction | A style line added to every picture. Pick one style and keep it. | "storybook watercolor illustration, soft ink linework, warm lantern light against cool blue fog" |
| Setting | What the world looks like, so clothes and backgrounds fit. | "A foggy canal city of stone bridges, gas lamps and rowing boats…" |
| Avoid | Words the picture model should stay away from. | "photograph, 3d render, modern clothing, cars, electric lights, text" |

Also in this tab: portrait shape, whether new characters get art offered, the "right now" card and where scene pictures appear. A **cover** (🖼 Art → Cover) and a **backdrop** (🖼 Art → Backgrounds) finish the package.

## 9. Side-panel trackers (⚙️ Settings → Story & narration → Side panel)

Choose **I choose the fields**, then add one tracker per label in your status line, with exactly the same label:

| Label | Type | Notes |
|---|---|---|
| Night | Number (Scene) | |
| Coins | Number | unit "coins" |
| Lanterns | Slider 0 to 10 | the main goal |
| Reputation | Status | options: Newcomer, Known, Trusted, Honoured |
| Level | Number | set by the app from XP |
| XP | Number | **levels at** "50, 150, 300, 500", **max level** 5 |
| Quests | Quest log | fills itself from the `[QUEST]` lines |

Values printed as `Label: value` are read for free, without an extra AI call.

## 10. Starter avatar (👤 Player)

A ready-made player character that fits the world: name, gender, age, looks, personality, background and abilities. Players can keep it or replace it. The example is **Rowan Hale**, a former canal-boat courier.

## 11. Export

**💾 Export & backups → Story package:**
- **All pictures** (or one per character for a smaller file);
- tick **Include my avatar sheet as a starter character**;
- tick **Smaller file: save pictures as WebP** to make the file several times smaller;
- **Download package.**

Turns, memory, notes, usage and API keys are never included.

## 12. Lore entries (📚 Lore, in the 🧠 Memory screen)

**What they are:** facts about your world that the app keeps and sends to the narrator **only when they matter**. Each entry has a title, **trigger words**, and the text the narrator should know. When one of its trigger words appears in the player's move or the last two turns, the entry is added to the narrator's instructions, hidden from the player. An entry ticked **Always send** goes out every turn. No AI is involved, so it costs nothing.

**Use them for** things that are too long or too situational for the prompt: a place's layout, a faction's customs, a rule you only need when it comes up, a character's history. The prompt stays short, and the narrator is still reminded at the right moment.

| Part | What to write | Example from The Lantern Market |
|---|---|---|
| Title | A short name (for you; the narrator sees it as a label). | Mothfolk manners |
| Trigger words | The words that should bring the entry up, separated by commas. Whole words, any capitalisation. Include names and nicknames. | Mothfolk, Kesh, antennae, wings |
| Text | 1 to 4 sentences of facts, written as plain statements. Say what must stay true; do not write instructions about style. | "Mothfolk speak softly and never raise their voices…" |
| Importance | 0 to 9: when many entries trigger at once, higher ones are sent first. | 7 |
| Always send | For a few short facts that should never be forgotten (tone, a core rule). | the "Tone" entry |

**For best results**
- Keep each entry to **one topic** and **short** (the app sends at most 8 entries and about 3,500 characters per move).
- Put the **secret** facts here too: the narrator is told them only when the clue words come up.
- Use the **Test** box in the dialog: type a sentence a player might write and see which entries it would trigger.
- Lore entries are **included in the story package** when you export it, and imported with it. (*Update this game from a package* adds entries the game does not have yet and never overwrites yours.)

## 13. Stat points and dice (optional, for stories with stats)

- **Points the player spends:** give your Points tracker a **spend points on** list (Settings → Story & narration → Side panel; for example `Strength, Stamina`) and an optional **max**. The side panel then shows **+** buttons, the app does the arithmetic, a window opens at the start of a new game to place starting points (read from the avatar sheet: "Strength 8, … Free points: 5"), and the narrator is told the final numbers and never assigns points itself. Players can add skills (for example a class's) in that window.
- **Dice:** the 🎲 button in ⚡ rolls a d20 + (stat − 5) against a difficulty and sends the outcome to the narrator as final. If your story prompt mentions rolls, checks or dice, the app also rolls **automatically** for risky actions; otherwise rolls happen only when the player presses 🎲. The player can change this in the same window.

## Checklist

- [ ] Prompt: world, player, characters, rules (with the secret), style, status line.
- [ ] Prologue (2 to 5 sentences) and opening (150 to 400 words, with a place tag and a portrait tag).
- [ ] 2 to 4 first moves and 2 to 4 endings.
- [ ] A cast card for every main character, each with a Normal 1 picture.
- [ ] Species described (if any), places with pictures, art direction, cover, backdrop.
- [ ] Trackers matching the status line labels exactly.
- [ ] A starter avatar, then export with the avatar box ticked.
- [ ] Lore entries for places, factions and secrets that are too long for the prompt (each with trigger words).
