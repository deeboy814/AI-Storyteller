# Changelog

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
- **Other names for characters:** an "Also called" box (so "Naomi" counts as "Dr. Okafor"), a merge for duplicates, and a "Names and forms of address" box (who may use which name for them) that is sent to the narrator every turn.
- **Two-tone story text (Settings → Appearance):** dim narration, bright dialogue, with an adjustable dimness.
- **Usage → Narrator cache:** how many replies read the prompt from Claude's cache and, for the rest, why it had to be written again (story prompt or settings changed, summary changed, earlier messages changed, cache expired, first turn since start).
- **Collapsible sections** in Settings, Art → Style & options and Art → Picture makers.

**Improved**
- **Cheaper picture and helper jobs.** Mood portraits for tag-style models use the character's saved tags plus built-in mood tags (no model call for the standard moods); scene pictures ask the model only for expressions, action, setting and framing; the new-character check runs only when a new name appears; the side panel reads values the narrator already prints ("Rank: Cadet  Sync: 96") without the AI.
- **Memory updates wait while Claude's cache is warm** (they run later, or when the cache has gone cold), so the next reply does not have to pay to write the cache again.
- Shorter portrait tags (`[[Name/mood]]`; the long form still works).
- **Safer saving:** several things save the same story (your reply, helper jobs, drawing jobs); a save now keeps what the others saved meanwhile instead of overwriting it.

**Fixed** (problems in 1.2.0 and earlier)
- Drawing a new mood picture rewrote the whole prompt in Claude's cache; the list of pictures now rides on your newest message, so only editing the story itself does.
- A story prompt that asked for more pictures was overridden by the built-in rule "most turns should have no scene tag"; the built-in rules now follow the prompt.
- The narrator almost never asked for a new mood, even when steered (use *Ask for exact moods*).
- Drawing a scene picture could undo changes made while it was being drawn (the side panel's latest update, edits to the story).
- A character you removed was added back by the next reply's new-character detection.
- The same character could end up twice in the cast ("Naomi" and "Dr. Okafor"); it is now detected and can be merged.

## 1.2.0
**New**
- **Windows support** (10/11): `install.bat`, `launch.bat`, `stop.bat`, `run.bat`, `run-lan.bat`, `uninstall.bat`, Desktop and Start-menu shortcut. Not yet tried on a real Windows machine.
- **⏻ Quit button** on the Home screen closes the app (no terminal needed).
- **Side panel tabs** (Info, Characters, Quests) that can be minimized, and a **Quest log** tracker type: expandable quests with objectives, rewards and status; completed quests show for one turn, then clear.
- **Art styles:** ready-made styles when creating a story, in the Art tab, and on AI & story (an Art style dropdown under Pictures that offers styles suited to your detected local checkpoints and API keys). New pictures match the style of a character's existing ones.
- **Your own picture types** (with an optional description) in the basic and extended sets; a new type the narrator asks for and you draw joins the extended set, and the narrator is told to prefer existing pictures.
- **New-character art (opt-in, Art tab):** named characters the narrator introduces are added to the cast with a short look, a 🎨 button in the side panel marks anyone without pictures, and a generic side-panel label ("Shop Girl") becomes the character's real name once they have a name and pictures.
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
