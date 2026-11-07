# Changelog

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
