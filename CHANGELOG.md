# Changelog

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
