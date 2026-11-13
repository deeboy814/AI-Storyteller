# Changelog

## 1.6.3
**New**

- **Character library (Home → 👥 Characters).** Make characters once and use them in any story. Describe them, draw 4 options in any story's art style (or with the default picture maker), pick one, or upload a picture. In a story's Cast screen, "👥 From the library" adds a copy with their look, face, notes and picture (the picture becomes their normal 1, so it is the face reference there). A character's page has "👥 Save to library" to keep an existing character for other stories.
- **Face in words (character page → Bio).** A box for the face in plain words, and "👁 Describe the face from their picture": a helper model that can see pictures (Claude, Gemini, ChatGPT, Grok) writes it from their normal picture. When filled in, it is put first in that character's picture prompts so makers that cannot take reference pictures (UnCanny, Chroma, Qwen) keep the same face. Empty means prompts are unchanged.
- **Face LoRA per character (character page → Bio).** Pick a LoRA trained on that character's face from ComfyUI's loras folder, with a strength and an optional trigger word. It is added whenever ComfyUI draws that character: portraits, the pick-from-4 options, scenes and the Now card.
- **The Images tab groups a character's pictures by category** (Expressions, Actions, Positions...), each group can be minimized (Minimize all / Expand all, remembered), and replaced pictures are a group of their own, "Earlier versions".
- **Story packages carry more.** The item list (🎒), "also called" names and generic-character marks now travel in a package.

**Improved**

- **The Lantern Market example uses every feature of the app.** Alternate openings, story beats (by move, night, a tracker and the road taken), lore, a world clock, dice, command buttons, points to spend, a replies-per-night counter, a Route choice and per-character Trust, items, generic characters, a character with no picture, intro panels and a fourth ending. Its prompt's GUIDE lines say where each feature is and how to try it.
- **Species & makers: picking a reference.** The 4 redrawn candidates now work like the other pick-from-4 lists: click one to see it full size, then "Use this one" under each. (Before, the tap did nothing.)
- **Character Images tab: an even grid** (4 per row, 3 on a tablet, 2 on a phone), every picture the same size.

## 1.6.2
**New**

- **Neural read-aloud voices (optional download).** Read-aloud voices window → Neural voices: natural-sounding Piper voices that run on your own computer. Nothing is installed until you press Download (the voice engine, about 35 MB, plus about 63 MB per voice, kept in the data folder). Eight voices to pick from, or type any Piper voice name. Each character can have a neural or a browser voice; if a neural voice fails, the browser voice takes over.
- **Image categories.** Every picture type belongs to a category you can rename, reorder, add and delete (Cast → Image categories…, and Settings → Picture makers). The defaults are Expressions, Actions, Positions, Finishes and Other adult. Each category has an adult flag, a shot (portrait or action) and an optional note that tells the narrator when to use it. Each picture type can be edited (what it draws, its framing, its tag words, which category and sets it is in), shows the exact prompt it sends, and can be changed for one story only. Your own types and categories are given to the narrator, adult ones only in adult stories.
- **Every category is a batch set.** Wherever you choose what to draw for a character, the list is Basic set, Extended set, then "All: <category>" for each category.
- **Every picture keeps its exact prompt (📝).** See, edit and redraw from the exact words a picture was made from; the old picture is kept as an earlier version.
- **Redraw a species' reference picture, pick from 4.** On the Species & makers screen, 🎨 draws four new candidates of a typical member from the species description with the maker chosen on screen, and you tap the one you want. Nothing changes until you pick.
- **Every picture maker is offered for species and characters.** The maker list shows every model of every picture service (Gemini, OpenAI, xAI, each ComfyUI model), grouped by service. The Species & makers screen also fits phone screens.
- **Inventory kept by the app (🎒, off by default, per story).** Items are cards with a quantity, an effect and an equipped mark. The narrator reports what you gain, use or lose and the app does the counting; undo and redo restore it. The side panel gets an Items tab, and "Read my items from the story" fills the list from the story.
- **Journal (📖).** A "Previously…" recap, the story so far, and a timeline of key events, finished quests and bookmarks. A "Welcome back" card offers a recap when you return after 6 hours or more.
- **Quest deadlines as a countdown** ("2 days left", "due today", "overdue by 1 day"), editable by clicking the badge; the narrator is reminded the day before.
- **Narration style presets.** Four one-click choices per story under Player → Notes: tone, pacing, reply length and writing style.
- **Separate adult tags per character.** An "Adult picture tags" box on a character's Images tab, used only for their adult pictures with tag-style makers.
- **Let phones and tablets restart the app (Settings → Phone & tablet, off by default).** Can only be switched on at the computer itself. A connected phone can then restart the app but never close it.

**Improved**

- **Adult pictures with one picture maker.** In an adult story without a separate adult maker, the main maker now draws explicit pictures with the adult wording. Reference-based makers copy only the face, hair and build from the references, not their clothes, and lying-down pictures are framed the right way up. Stories with adult content off are unchanged.
- Nudity tags saved on a character are no longer added to ordinary pictures.

## 1.6.1
**New**

- **Check for updates (off by default).** Settings → Libraries & backup → Updates has a "Check for updates when the app starts" box and a "Check for updates now" button. The app asks GitHub's public release page whether a newer version exists and, if so, shows a small card with a link. Nothing about you or your stories is sent, and nothing is downloaded or installed for you.
- **Generic characters.** Cast → Cast tools → Generic characters… adds throwaway characters for unnamed people (a waiter, a guard, a passer-by): pick from a list of 15 roles, ask the app to suggest roles that suit the story's world, or type your own, and optionally draw a normal picture for each. The narrator uses them by role whenever someone unnamed appears, never invents a name for them, and switches to the real character once the story names one. They show as one "Generic Characters" card.
- **More adult picture types (adult stories).** New built-in types for sexual positions (riding cowgirl, riding reverse cowgirl, doggy-style, blowjob, missionary, anal) and for how a scene ends (creampie, anal creampie, face cum, mouth cum, swallow, ass cum, pussy cum). Each has its own wording and framing, in both prompt styles, and shows only the one character with the partner out of frame. The narrator is told to use them only while the moment is actually happening, to change the tag only when the position changes, and to finish with afterglow.
- **A story can reword a picture type for itself.** A story's art settings can replace the wording and framing of any picture type (for example a different look for "dead" or "combat") while the mood tag stays the same, so the narrator's tags and the app's checks keep working.

**Improved**

- **Picture makers that cannot take reference pictures** now draw character faces as one close head-and-shoulders portrait. No more full-body figures, sheets of several views, or the character's usual expression overriding the mood. More expression names (grin, smirk, glare, pensive...) count as faces.
- **Adult pictures** drop everyday clothes and props from the character's look, so the picture follows the scene.
- **Busy online models are retried.** When a service answers "server busy" (503 or 529), the app waits a moment and asks again, up to 3 times, before showing an error.

**Fixed**

- **Numbers with a thousands comma are read correctly in side-panel trackers** (7,980 was stored as 7.98; 12,400 is now 12400).

## 1.6.0
**New**

- **New game-like stories are asked about dice.** A story that tracks stats and levels (not a real-life one) gets a one-time question when it is first opened: automatic dice (adds a short dice rule to the story prompt), only when you press 🎲, or no dice. It is also asked when the prompt already calls for rolls, so you can switch them off. Never asked again, and never for stories where dice were already chosen (a package can set them).
- **The latest dice roll shows in the side panel.** A small card at the top (stat, d20 + bonus, total against the difficulty, outcome) for the move that was rolled; it disappears as soon as a move without a roll is sent.
- **Pick the picture maker for a cover.** In a story's ⋯ menu on the Home screen, the cover dialog has a "Picture maker for the cover" list of every picture service and model you can use (local ComfyUI checkpoints and FLUX are free; online ones say they bill separately and are greyed out without a key), so you can try different ones for the same cover. The choice is remembered for that story.
- **Dice checks understand letter-grade stats.** A stat written as a grade with an optional gauge (`C-[40/100]`, F to SSS) is now rolled against as a number (F is 1, B is 5, SSS is 10), so stories that track stats as grades get automatic and manual checks too. Body, Mana, INT and Divinity are also matched to the actions they govern.
- **Dice stay meaningful at high stats.** A stat's bonus is still stat − 5 up to 10; above 10 it eases off (+3 per doubling: 20 is +8, 40 is +11, 80 is +14) so a stat of 30 or 100 from many level-ups no longer makes every check automatic.
- **Story packages can carry the world clock and dice settings** (✏️ Edit story → Options and the 🎲 window). An author whose narrator keeps the time itself ships the clock off; a story that never uses rolls ships dice as "only when I press 🎲". Both are exported, imported with their values cleaned, and left at their defaults when a package does not have them. (app/main.py `PACK_FIELDS`, `import_pack`)

**Improved**

- **Story cards on the Home screen show the whole cover.** Every card now has the same portrait picture area (the proportions of a typical portrait cover, 600 × 914) instead of a wide 16:9 strip that cut covers off; more cards fit per row, and phones show two per row. Covers the app draws (🖼 Art → Cover art…) are now drawn in portrait to match.
- **Avatar: "🪄 Build from story".** In Player → Avatar, a button that fills only the empty boxes (gender, age, appearance, personality, background, abilities) from the story prompt, fitted to the world and to how newcomers start in it, for players who only want to type a name. You review the boxes before saving; what you already wrote is kept. One text-model call.
- **Alternate openings.** A story can carry other ways to begin. Before the first move, a row of buttons above the opening scene lets the player choose (the two openings simply trade places, so you can switch back). Authors add them in the story editor → Story start → "Alternate openings" (a name and a scene each, up to 6) and they travel in story packages.
- **Home first.** The app opens straight to the Home screen. The story screen stays hidden while the app starts, so it no longer flashes up first.
- **First-run set-up.** A new install shows a short welcome pop-up on the Home screen: which AI writes the stories, which one draws (with the same recommendations and key boxes as Settings), and an optional monthly spending warning. It appears once; Skip is fine, and every choice stays in Settings. Existing installs never see it.

**Fixed**

- **The Save button on 🖼 Art → Picture makers was hidden.** The tab folds its settings into sections, and the folding swallowed the Save row into the last (closed) section, so changes there could not be saved. Save now stays outside the sections and sticks to the bottom of the window (also on the Style tab), and the tab says "Unsaved changes" until you press it. Checked on desktop and phone width, and that a saved change persists.
- **Side panel on a phone no longer closes when you change its tabs.** Tapping a tab redrew the panel, so the app took the tap for one outside it.
- **The keyboard no longer pops up over the story when a reply finishes writing** (touch screens only; computers still put the cursor back in the box).

- Local-model mode is now "steadier writing" for every provider: the cut-off trim (Claude and OpenAI-style models now report when a reply hit the length limit), format repair, variety note and length note work with online models too. Context guard, JSON forcing, sampling, helper ordering, free scene/right-now card and batched key events stay Ollama-only, because online models gain nothing from them (the free scene prompt read noticeably worse than the AI's on real Claude replies).

- Claude replies now honour Max reply tokens: the extra thinking headroom is only added when Claude thinking is on (before, it was always added, so the limit was ignored).

- **Turns between scene pictures.** In a story's Art options → Scene pictures, a new "Turns between scene pictures" number (1 to 20, default 1 = no limit) spaces the pictures out: 3 means at most one every third reply. The narrator is told to skip the frame on the replies in between, and the app drops one if it is added anyway.

- **Character pictures.** Deleting an earlier (replaced) version of a picture no longer asks "are you sure". The character card's Images tab has a new "Delete all their pictures" button that removes every picture of that character in the story (all moods and earlier versions); it asks once, because it cannot be undone.

- **Name your pictures.** Uploading a picture to a character now asks what to call it (the narrator picks pictures by name, such as smile or normal 1), the name box is labelled, and every picture has a ✎ button to rename it later.
- **Species traits.** Art → Species has a new "Story traits" box (abilities, weaknesses, customs) that is given to the narrator, and a "Write both from the story" button that drafts the look and the traits from the story prompt and the species' members. Nothing is saved until you press Save.
- **Reframe and bigger pictures.** Every scene picture has a Reframe menu (tall, wide, cinematic 16:9, square) that redraws it in that shape. Art options gain a "Scene picture size in the story" (normal, large, full width), a "Wide (16:9)" shape, an Extra large portrait size, and a drawn-size option for local ComfyUI (1.25x or 1.5x canvas).

- **LoRAs for local picture makers.** Under Art → Picture makers → Local (ComfyUI) → "Settings for this checkpoint only", a new LoRAs box lists the files in ComfyUI's loras folder (pick one, set a strength, Add; up to 6 per checkpoint). They are chained onto that checkpoint's workflow, for SDXL-style checkpoints and FLUX.2 alike, and also apply when FLUX draws backgrounds, places or a species' characters. Online picture makers (Gemini, OpenAI, Grok) do not take LoRAs, so the option is local only.

- **LoRAs for explicit pictures.** Art → Picture makers → Adult (NSFW) pictures has a LoRAs box in "Settings for this model", used only for that model's explicit pictures (name @ strength, a second after a semicolon).

- **First-start set-up pop-up.** The first time a story is opened (before its first move), one pop-up asks who you are (the story's own character, a saved avatar, or a blank sheet with clues read from the story prompt about what fits its world, plus "Fill it in for me"), the art style, which AI draws the pictures, and adult content. It runs before the stat-points and dice questions, so those see who you are. Skip it or change any answer later (Art, Settings, Player).
- **Tabbed side panel.** The right panel now has underline tabs (Status, Relations, Cast, Items, Quests; only the ones the story has data for), a "you" card with your picture and place, and cards with a picture for each character. Relationship bars have their label above.
- **Stories panel (left).** A new left panel lists your stories with search, Recent / All, a New story button and shortcuts (Home, Avatars, Lore library, Art, Usage, Settings). Open it with the 📚 button; it remembers whether it was open and has an edge tab to bring it back. On a phone there is no side column: the ☰ menu has 📚 (a window with the same story list, search and New story) plus Avatars, Lore library and Usage buttons.

- **Adult pictures follow the picture again.** Explicit mood portraits and scenes made by the adult picture maker (an SDXL model) came out as the character in their everyday outfit holding their props, whatever the mood. Three causes are fixed: the character's outfit and props are left out of the prompt (face, hair, build stay; what they wear comes from the picture's own description), the prompt is short and subject-first instead of a long list of instructions, and only the face of the reference picture is used (a tighter crop, so its clothes and street do not carry over). In a photorealistic story, the model's extra "painterly" style words and "photo" negative words no longer fight the story's own style. Redraw any adult pictures made before this.

- **Dice: the stat is picked from what you do, not what you say.** A move that was only talk could roll Strength (for example "force a small shrug"), and handing out stat points could roll a stat. The automatic dice now match physical stats (Strength, Reflexes, Stamina...) only against the character's actions outside quotation marks, "force" counts only for forcing a door, lock or way through, a move that ends in a question (even inside quotes) or that assigns stat points is not rolled, and a stat named only in passing no longer counts. Social stats (Charisma...) also read what is said aloud, with more social words (apologising, pleading, flirting, comforting, smoothing things over).

- **Starting values for a story's own trackers.** A tracker the author builds can have a start value (a number, or one of its labels), so it does not begin empty: for example Hunger, Thirst and Fear can start at 75, 95 and 0, and a choice tracker can start on "undecided". The value comes with a story package.

- **Set-up pop-up: "Decide later" and an empty blank sheet now really give no character.** A story that came with a starter character (a package's own player character, say) kept it when you picked "Decide later" or left the blank sheet empty. Both now clear it, so the story starts with the plain "you". "Skip setup" still leaves everything as it was.

- **Home screen settings are app-wide defaults.** Settings, Picture makers, Usage and Avatars opened from the Home screen no longer act on the story you opened last (its content switch, its AI choices, its usage filter). The open story is set aside while the window is open and comes back when it closes.

- **Command buttons.** A story can define its own buttons above the input box (story editor → Options → Command buttons, or shipped in a package): send a line as your move, put one in the box to finish, or open the stat-points window with no AI call. A story can have buttons such as Assign points, Status or Shop.

- **Update from a package carries more.** Updating a game in progress from a newer package now also adds the package's story beats and lore entries the game lacks (yours are kept), its command buttons, its world clock (when the game's is on "let the prompt decide"), and the starting values of new trackers.

- **Easier recovery and updating.** The story card's ⋯ menu on Home has a new "⟳ Update from a package…" button (the same update as 💾 Export & backups inside a story). Updating now also puts back pictures that are missing (characters with none, places, species, cover and backdrop), and a story restored from a deleted-stories backup no longer shows broken pictures when its files were deleted with it.

- **Lore as readable cards.** The Lore window now shows each entry as a read-only card (title, importance, trigger words as chips, the full text) with an ✎ Edit button that turns that one card into the editing form (Done goes back, Save keeps it). There is a search box over titles, trigger words and text.

- **The prompt style follows the model.** Art → Picture makers remembers the prompt style (tags or sentences) per picture model: pick another model and it switches to that model's remembered style, or the usual one for its kind (anime checkpoints: tags; others: sentences). Change it for a model and that is remembered; a style set before this update is remembered for the model it was set for. The drawing code uses the remembered style for whichever model draws (the story's, a character's own, or the adult maker).

- **Chroma picture models.** Chroma diffusion models in ComfyUI's `diffusion_models` folder now show up in the model lists (story, Adult pictures, backdrops) and are drawn with their own built-in workflow (`workflows/comfyui-chroma.json`: T5 text encoder, `ae.safetensors`, negative prompt, 26 steps). Sentence-style prompts; no reference pictures.
- **Phone keyboard.** After you send a move on a phone or tablet the keyboard now closes and stays closed while the reply is written.
- **Diffusion models are recognised by what they are, not what they are called.** The app reads the layer names in the model file's header to tell Chroma-type and FLUX.2 models apart, so finetunes with any name appear and draw with the right workflow. File names are only the fallback.
- **Qwen-Image picture models.** Qwen-Image diffusion models are recognised and drawn with their own workflow (`workflows/comfyui-qwen-image.json`: Qwen2.5-VL text encoder, negative prompt; Lightning versions get 4 or 8 steps and cfg 1). Qwen-Image-Edit is left out because it needs a picture to edit.
- **Backdrop and Places maker follows the model.** Picking a model in the separate backdrop maker fills the workflow box with the built-in workflow that suits it (FLUX.2, Chroma, Qwen-Image, or a plain one for an ordinary checkpoint); a workflow you edited yourself is never replaced. Checkpoints can now be chosen there too. Built-in workflows use the text-encoder and VAE files ComfyUI actually has (for example the 9B FLUX.2 gets the 8B text encoder).
- **Days of the week.** When a story's prompt names days of the week (two or more of Monday to Sunday, "day of the week", a weekly schedule) or it has a Weekday value, the app now keeps Monday, Tuesday… itself and tells the narrator each turn. It moves on with the numeric Day value (Day 3 is two days after Day 1), or when the world clock passes midnight; a Weekday the narrator prints is adopted, and a Weekday status value is filled in by the app. ✏️ Edit story → Options → Days of the week sets Auto/On/Off and which weekday Day 1 is (the prompt's own "Day 1 is a Monday" is used when set to auto). Click the clock/day in the side panel to set it by hand (for example "Friday 2pm"). Packages carry the setting.
- **Chroma tuned for character portraits.** Chroma and Qwen-Image cannot take reference pictures, but the prompts still told them to "copy the face from the references"; the app now leaves that out for them. Their portraits also drop the world's setting (they painted the whole street behind the face) and ask for a plain studio backdrop with a matching negative prompt, and each character gets a steady seed so the face stays the same across moods. A Chroma speed LoRA (names containing flash, turbo, lightning or hyper) automatically switches the workflow to the few-step settings it needs (8 steps, Heun, cfg 1).
- **Per-story picture wording.** A story can reword what a picture type looks like for itself (for example its "dead" or "combat" pictures) without changing the mood name the narrator uses, so the narrator still picks the same pictures. It is stored in the story's art settings (`mood_looks`, `mood_framing`, `mood_framing_tags`, each a map from the mood name to text).

## 1.5.1
**Fixed**
- **The Save button on 🖼 Art → Picture makers was hidden.** The tab folds its settings into sections, and the folding put the Save row inside the last (closed) section, so changes there could not be saved. Save now stays outside the sections and sticks to the bottom of the window (also on the Style tab), and the tab shows "Unsaved changes" until you press it. Checked on desktop and at phone width, including that a saved change persists.

## 1.5.0
**Tested on a phone.** Phone and tablet play was checked on a real Android phone over a home network (PIN, remembered device, playing a story) and at phone width in a desktop browser: every screen and dialog (38 of them) was opened with no sideways overflow and no script errors, the phone loads nothing from the internet, and a phone cannot read the PIN, close the app or make a bug report. iPhone, iPad and Windows have not been tested.

**New**
- **Story beats (📅).** The story's own schedule of key events, read from its prompt (or added by you) and tied to the side panel ("when Day reaches 7"), the move count or "on my next move". When one is due, the app sends it to the narrator hidden, so the story stays on plan without you typing it; the next beats are named as "coming up, do not reveal" so it does not drift. Hidden behind a spoiler blur until you tap it; editable; included in story packages.
- **Lore (📚).** Facts about your world with trigger words, sent to the narrator hidden only when a trigger word comes up (or always). Editable, with a Test box; included in story packages (the example package ships five entries and a guide section).
- **Continuity check (🧠 Memory).** The AI reads the whole story for contradictions and proposes the smallest repair for each; you tick which to make, it backs up first, keeps the old wording in the edit history, and checks again.
- **Stat points you spend with buttons.** A points tracker lists the stats and skills it is spent on; + / − buttons in the side panel, an assign window at the start of a game (values read from the avatar sheet), skills can be added, and the narrator is told the final numbers and never assigns points itself.
- **Dice checks (🎲).** The app rolls a d20 + (stat − 5) against a difficulty and the narrator must narrate the outcome. Automatic when the story prompt mentions rolls or checks, otherwise only when you press the button, or never.
- **World clock (optional per story).** The app keeps the in-world time of day (on when the prompt talks about a clock, or by your choice), resets it each Day, and tells the narrator, so the time stops drifting.
- **Quest deadlines.** "Deadline: Day 9" is remembered: a ⏰ badge, and the narrator is told when a quest is due or overdue.
- **Turn counter tracker.** A number tracker can count replies since another tracker changes (the turn of the day), kept by the app, never by the narrator.
- **Story commands.** ▶️ Continue (the narrator carries on by itself), ⚔️ Continue the fight, and, in adult stories, 🔞 Full adult scene for narrator models that fade to black. Sent hidden with your move.
- **Let the narrator decide some of my character's routine actions** (story option, off by default).
- **Fill a character's whole sheet from the story**, with a warning not to use it early, and a review of every box before anything is saved.
- **Action moods.** An Action set (combat, strike, guard, dodge, charge, cast, wounded, victory…) and a separate Adult action set that exists only in adult stories, drawn with the combat framing (upper body, whole picture on click); the narrator prefers them in fights, chases and danger (your choice).
- **Restart a story from the beginning, as a separate copy** (Home → ⋯). Works on imported saves too.
- **Report a bug (🐞).** One .zip with how far your game got, the problems the app finds by itself, recent errors and your settings, with personal data removed (keys, PIN, e-mails, phones, links, IP addresses, file paths, your user name and, by default, character names). You read what is in it first; nothing is uploaded.
- **Import a save works without a story prompt** (the AI writes one from your text), keeps the last paragraphs of plain text word for word, and reads JSON chat exports (ChatGPT, Claude, SillyTavern) and common transcript labels.
- **Free checks that need no AI:** format and number checks on every reply (a doubled or misplaced status line, Day or XP going backwards, XP past the next level, stat points changed without you spending them), a status-line tidy, a consistency lint (a dead character speaking, a misspelled name, eye or hair colour against the card), key events from notifications (*Level 3 reached*, *Life lost*…), merge suggestions for duplicate characters, and suggested moves in the ⚡ bar. Plus fewer AI calls: free place detection for the automatic background, and optional free "Right now" cards and scene prompts.
- **Phone:** devices that entered the PIN are remembered (also after a restart), with a Forget button in Settings; a small tab on the right edge (or a swipe) opens the status panel; a slim header with a ☰ menu.

**Improved**
- The Art screen keeps one size on every tab, and the redundant Picture makers card is gone from its Overview.

**Fixed** (problems in 1.4.0 and earlier)
- Button rows in dialogs ran off the edge of a narrow screen (for example *Backups…* in Memory); they now wrap.

## 1.4.0
**New**
- **Play from a phone or tablet on your home network (option, off by default).** Settings → Phone & tablet: one switch makes the app open to your home network; the tab shows the address to open and a PIN. Devices enter the PIN once. Requests from outside the home network are refused, the phone loads nothing from the internet (everything comes from this computer), and phones cannot see the PIN or close the app.
- **Species, each with a reference picture.** Every character can have a species. Under 🖼 Art → Characters → *Species & makers…* you describe the whole species (added to every member's picture prompt) and choose its default picture maker. The first picture of a new species becomes its reference, so the next member shares the species' look but gets their own face. New characters introduced as "Species: …" are filed under it automatically.
- **Per-character picture maker:** the story's own, **FLUX.2 klein with reference pictures** (best for non-human characters), or another ComfyUI checkpoint. FLUX.2 models now also appear in 🖼 Art → Picture makers and can be the story's main picture maker.
- **A separate picture maker for adult pictures (option, off by default).** Shown only while the open story has *Adult (NSFW) content* on, and used only for explicit pictures in such stories; everything else keeps the normal makers. The list of what counts as explicit is editable. Each model has its own settings for these pictures only (face match, guidance, steps, CLIP skip, refine, shape, extra words and an optional different art style), which never affect its ordinary pictures.
- **"Right now" card can draw what is happening:** keep the character's saved mood picture, or draw a new picture each turn of them doing and wearing what the card says (only redrawn when that changes).
- **Regenerate with changes.** Retry opens a box to describe what should change (sent once, not saved); the scene picture is redrawn when needed.
- **Every retry attempt is kept.** Retries start from the previous turn only: what the discarded reply changed (side panel, new characters, key events) is undone unless you tick what to keep. Arrows (◀ 2/3 ▶) and a list on the newest reply swap an earlier attempt back in.
- **Redo** after Undo, until you send a new move, and **edit history** (up to 10 earlier versions of an edited reply or move).
- **A new "normal 1" offers to redraw all of that character's pictures** from the new face.
- **Levels from XP (option).** Give an XP tracker a **levels at** list (for example `50, 150, 300, 500`) and an optional **max level**: the app keeps the Level tracker right, shows the next goal ("XP 160 / 300") and tells the narrator, so it only has to announce level-ups.
- **Quest journal:** finished and failed quests are kept under the quest log.
- **Rule check (story option):** a small helper checks each reply against the story's rules and flags clear slips, with **Fix this reply** or Ignore.
- **Story packages:** can include a starter avatar (text only), can save pictures as WebP for a much smaller file, show real import progress and a fuller summary, and can **update a running game** with a newer package (turns, memory and existing characters are kept; a backup is made first).
- **Example story package** (`examples/Example Story Package - The Lantern Market.zip`): a complete, original, playable story with pictures, showing how to set up every part, plus a step-by-step guide (`examples/lantern-market/HOW-TO-BUILD-A-STORY-PACKAGE.md`).
- **Deleting a story asks about its pictures**, and Home → 🗂️ **Storage** restores recently deleted stories, shows disk use per story and cleans up leftover pictures and caches.
- **Quit also offers Restart**, and an **"Update waiting"** banner appears when the app's files are newer than the running app.
- **See-through main screen** (Settings → Appearance): let the story's backdrop show behind the text and side panel.

**Improved**
- **Settings regrouped** into seven tabs with like settings together: AI models, Story & narration, Memory & cost, Appearance, Phone & tablet, Local models, Libraries & backup. Nothing was removed.
- **Clearer buttons:** one consistent icon style, size and alignment, with separators between groups. Symbols some systems could not draw were replaced. The per-reply tool bar sits under each reply.
- **Action pictures are framed on the upper body** (combat, dead, and any activity rather than an expression); clicking opens the whole picture. Sentence-style picture makers such as FLUX now get the full description of each picture type. **Combat** looks like a real fight and **dead** shows a lifeless body with the face visible (more graphic only in adult stories).
- **Quest logs no longer go quiet:** with a quest tracker and no active quest, the narrator is reminded to offer quests.
- Up to **24 side-panel trackers** per story (was 16).
- ComfyUI is not stopped by its idle timer while a picture job runs.
- Long explanations in dialogs fold to two lines with "More"; the Retry dialog remembers your ticks; cast cards show species and picture-maker badges.
- Examples in hints and help text are generic.

**Fixed** (problems in 1.3.0 and earlier)
- The page jumped to the end of the reply when a picture finished or an art dialog closed.
- Cast cards cropped upright portraits into a wide frame.
- Fields in a character's Bio tab and text boxes in Art → Style & options ran past the dialog edge.

## 1.3.0
**New**
- **🖼 Art menu: every picture tool in one place.** An overview of each kind of picture with its status (characters, your avatar, Places, Backgrounds, cover, intro panels, scene pictures, picture makers), each opening its tool, with a **← Art menu** button on every screen to come back. *Style & options* holds the art direction, setting and every picture switch; *Backgrounds* holds the backdrop tools; *Picture makers* holds the picture services (moved here from Settings → Pictures; also reachable from the Home screen). ⭳ is now **Export & backups** (text, illustrated page, story package, this story's backups, whole-app backup).
- **Character memory (Settings → Memory & cost, on by default).** Each character's key events live on their card and are sent to the narrator only while they are in the scene, in a short note on your newest message (size: Small, Medium or Large). The shared memory summary stays short (plot, places, items, open threads). The helper AI condenses a long log by itself (your own entries are never touched), and an old summary's "Key characters" part moves onto the cards the first time it is updated (a backup is saved first).
- **Append-only memory summary.** A memory update writes only the new entries and a refreshed status paragraph and the app appends them, instead of rewriting the whole summary. On a 70-reply story: about half the input and an eighth of the output tokens of a rewrite, and 5 seconds instead of 23. If the model's answer does not fit, the old full rewrite is used.
- **Small helpers (optional second helper model).** Pick a cheaper or local model for the short jobs after every reply: key events, new characters, side panel, the background place check, suggested replies and picture prompts. Default: the same as Recaps & helpers.
- **Places:** fixed pictures of rooms the story keeps returning to (name, describe, draw 4 options, pick). The narrator shows one with a tag when a scene starts there.
- **Scene pictures most turns (option)**, and a choice of where scene pictures show: under the reply, a wide **banner above or below the narration**, or in the side panel's Right now card.
- **Right now card (option):** the side panel shows who you are talking to (picture for their current mood, what they are doing, what they are wearing) in place of your avatar, with a small button to flip back.
- **Automatic background (option):** after each reply the helper AI says where the scene is and the screen backdrop follows: a Places picture or an earlier background is reused, a new place gets a new one. **Dramatic-moment backgrounds (option)** draw a variant for an alarm, fire, power failure or battle and return to the plain one afterwards. Both switches are in 🖼 Art → Backgrounds.
- **Separate picture maker for backgrounds and places (option, ComfyUI):** for example FLUX.2 klein, which draws scenery well; includes a built-in workflow and a test button. Characters, scene pictures and covers keep the maker you chose.
- **Per-checkpoint picture settings (ComfyUI):** reference strength, where the reference stops applying, refine denoise, steps, extra words, whether to use the refine pass, how many references to use and whether to crop them to the face, each saved per checkpoint, so an anime and a realistic checkpoint can be tuned separately. The picture you chose for a character ("normal 1") is always the main reference.
- **Character picture tags:** for tag-style picture models the helper writes each character's look tags once; you can read and edit them (character page → Images → Picture tags) or have them rewritten, and they are used in every portrait and scene picture.
- **Extended mood set (option):** new characters get the extended set of mood pictures by default, and from the second turn on the narrator is reminded each turn to use the whole range instead of mostly normal and smile.
- **Ask for exact moods (option):** the narrator names a character's real feeling and uses a listed mood only when it is an exact match, so you are offered a new picture for each new mood.
- **Cover and avatar pictures: pick from 4.** The cover editor shows the picture description (edit it, or have it written from the story), then "Draw 4 options"; avatars work the same way. Unchosen options are removed after a day.
- **Other names for characters:** an "Also called" box (so a nickname counts as the same character), a merge for duplicates, and a "Names and forms of address" box (who may use which name for them) that is sent to the narrator every turn.
- **Two-tone story text (Settings → Appearance):** dim narration, bright dialogue, with an adjustable dimness.
- **Usage → Narrator cache:** how many replies read the prompt from Claude's cache and, for the rest, why it had to be written again (story prompt or settings changed, summary changed, earlier messages changed, cache expired, first turn since start).
- **Collapsible sections** in Settings, Art → Style & options and Art → Picture makers.

**Improved**
- **Cheaper picture and helper jobs.** Mood portraits for tag-style models use the character's saved tags plus built-in mood tags (no model call for the standard moods); scene pictures ask the model only for expressions, action, setting and framing; the new-character check runs only when a new name appears; the side panel reads values the narrator already prints ("Health: 80   Gold: 12") without the AI.
- **Memory updates wait while Claude's cache is warm** (they run later, or when the cache has gone cold), so the next reply does not have to pay to write the cache again.
- Shorter portrait tags (`[[Name/mood]]`; the long form still works).
- **Safer saving:** several things save the same story (your reply, helper jobs, drawing jobs); a save now keeps what the others saved meanwhile instead of overwriting it.

**Fixed** (problems in 1.2.0 and earlier)
- Drawing a new mood picture rewrote the whole prompt in Claude's cache; the list of pictures now rides on your newest message, so only editing the story itself does.
- A story prompt that asked for more pictures was overridden by the built-in rule "most turns should have no scene tag"; the built-in rules now follow the prompt.
- The narrator almost never asked for a new mood, even when steered (use *Ask for exact moods*).
- Drawing a scene picture could undo changes made while it was being drawn (the side panel's latest update, edits to the story).
- A character you removed was added back by the next reply's new-character detection.
- The same character could end up twice in the cast (under a nickname and under their full name); it is now detected and can be merged.

## 1.2.0
**New**
- **Windows support** (10/11): `install.bat`, `launch.bat`, `stop.bat`, `run.bat`, `run-lan.bat`, `uninstall.bat`, Desktop and Start-menu shortcut. Not yet tried on a real Windows machine.
- **⏻ Quit button** on the Home screen closes the app (no terminal needed).
- **Side panel tabs** (Info, Characters, Quests) that can be minimized, and a **Quest log** tracker type: expandable quests with objectives, rewards and status; completed quests show for one turn, then clear.
- **Art styles:** ready-made styles when creating a story, in the Art tab, and on AI & story (an Art style dropdown under Pictures that offers styles suited to your detected local checkpoints and API keys). New pictures match the style of a character's existing ones.
- **Your own picture types** (with an optional description) in the basic and extended sets; a new type the narrator asks for and you draw joins the extended set, and the narrator is told to prefer existing pictures.
- **New-character art (opt-in, Art tab):** named characters the narrator introduces are added to the cast with a short look, a 🎨 button in the side panel marks anyone without pictures, and a generic side-panel label ("Innkeeper") becomes the character's real name once they have a name and pictures.
- **Adult-content switch now covers pictures** (scene pictures and portraits; adults only; covers and backdrops stay safe for work).
- Settings: picture services are collapsible cards (Gemini, Grok, OpenAI, Local) whose model matches the AI & story chooser; picture prompt style follows the checkpoint type.
- Every story prompt is automatically made safe for the app's picture system (outside image keywords and links are ignored).

**Improved**
- **Rename a story** from the Home screen or the story's header: hover the title and click the ✎, or use the Title box in the ⋯ menu on a Home card. Both places stay in sync (the saved files are not renamed).
- **Click any picture to see it full size** in an overlay (story, a character's own page, galleries, covers in the editor; the character cards in Cast & gallery still open the character): Esc or a click outside closes it, ← → move between pictures, a click on the picture toggles actual size.
- Mood pictures (portraits and scenes) are prompted to show the mood clearly in both the face and the body language, with fuller descriptions of each standard mood.
- Custom text trackers hold up to 400 characters; editing trackers no longer wipes the values the AI is keeping.
- **Begin the story** gives the narrator a proper instruction to write the next scene from the opening, prompt, notes and character.
- Web pages from other sites can no longer press buttons in the app (cross-site and DNS-rebinding protection).

**Fixed**
- Read-aloud sounded garbled: it used the browser's first or default voice, which on Linux is often not English. It now picks the best voice for the story's language, reads in short sentences, skips status blocks, hidden tags, links and emoji, and the Voices window warns when no voice for your language is installed.
- A story that comes with its own trackers lost them when the side panel was set to "Leave it to the prompt author", so the panel stayed empty. Its own trackers now keep showing and updating in that mode.
- A failed move (deleted story, AI error) could leave the page stuck on "Writing…" and wipe the error message; the error now stays and you can try again.

## 1.1.0
Your own side-panel trackers: type a name, pick where it lives (you, scene, each character) and its look (slider, number, status, text); the helper AI suggests settings, keeps them updated each reply, repairs values that don't fit, and fills new ones from the story so far.

## 1.0.0
First public release: story creator (prompt, cast, prologue, opening and suggested moves, trackers, endings, media), play screen with side panel and memory, cast portraits and picture generation (online or ComfyUI), per-story AI choices for Claude, ChatGPT, Gemini, Grok and local Ollama, usage and spending limit, local-model on-demand management, whole-app backup, story packages, appearance options, desktop launcher.
