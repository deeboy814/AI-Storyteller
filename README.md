# AI Storyteller

A private, browser-based game where an AI plays the narrator. You write (or import) a **story prompt**: the world, the characters, the rules and the writing style. Each turn you type what your character does, and the AI narrates what happens next, remembering the story as it grows. It can also keep a **cast** with portraits, track stats in a **side panel**, draw pictures, and read the story aloud.

Everything runs on your own computer. Your stories, pictures and settings are stored in the `data` folder next to the app. The only things that leave your computer are the text sent to the AI service you choose (Claude, ChatGPT, Gemini or Grok), if you use one. Local models (Ollama, ComfyUI) send nothing anywhere.

**Contents:** [Download and install](#download-and-install) · [What you need](#what-you-need) · [Starting and stopping](#starting-and-stopping) · [Before your first story: settings to check](#before-your-first-story-settings-to-check) · [Quick start](#quick-start-five-minutes) · [Using the app](#using-the-app) · [Settings reference](#settings-reference) · [Troubleshooting](#troubleshooting) · [Technical notes](#technical-notes)

---

## Download and install

1. **Download:** on the project's GitHub page press the green **Code** button → **Download ZIP**, then unzip it. (Or run `git clone https://github.com/deeboy814/AI-Storyteller.git`.)
2. **Open a terminal in the unzipped folder** (right-click inside it → *Open in Terminal*) and run:

   ```bash
   ./install.sh
   ```

   It checks Python, installs the app's libraries into the folder (nothing system-wide), adds an **AI Storyteller** icon to your Desktop and application menu, and offers to install Ollama (free local models). Answer the questions, or run `./install.sh --yes` to accept the optional steps. It is safe to run again.
3. **Start the app:** double-click **AI Storyteller** (or run `./launch.sh`). Your browser opens the app at `http://127.0.0.1:8000`. Everything stays on your computer.
4. Open **⚙ Settings** and follow [Before your first story](#before-your-first-story-settings-to-check).

It runs on Linux (tested on Ubuntu). It needs **Python 3.10 or newer**. To remove the desktop icon later run `./uninstall.sh` (your stories are not touched).

Want something to try? Paste `examples/the-lighthouse-keepers.md` (a small original story prompt) into **New story**; `examples/story-prompt-template.md` explains how to write your own.

## What you need

- A computer running **Ubuntu Linux** (the instructions below are for Ubuntu).
- **At least one AI to narrate**, either:
  - an **online AI** (Claude, ChatGPT, Gemini or Grok): you create an account with the company, add a payment method and get an *API key* (a long password for apps). Online AIs are the easiest and write the best stories, but they cost money per use (typically a few cents per turn). They bill separately from any chat subscription you may have. Where to get a key:
    - Claude: console.anthropic.com → API Keys
    - ChatGPT (OpenAI): platform.openai.com → API keys
    - Gemini (Google): aistudio.google.com → Get API key
    - Grok (xAI): console.x.ai → API keys
  - or a **local AI** through *Ollama* (free, private, runs on your graphics card). Quality depends on your graphics card: 12 GB of video memory runs a mid-sized model well; 8 GB or less is limited.
- **Optional, for pictures:** either an online picture service (Gemini, OpenAI or Grok, billed per picture) or *ComfyUI* on your own graphics card (free; needs about 6 GB of video memory or more).

## Starting and stopping

Double-click **AI Storyteller** on your Desktop (also in the application menu). It starts the app in the background if needed and opens it in your browser. Right-click the icon for **Stop AI Storyteller**. Without the icon: `./launch.sh` starts it and opens the browser, `./run.sh` starts it in a terminal, `./stop.sh` stops it. If something goes wrong, the log is `data/server.log`.

**Optional local models** (if you skipped them in the installer):

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama pull mistral-nemo      # about 7 GB, runs well on a 12 GB graphics card
```

## Before your first story: settings to check

Open **⚙ Settings** (from the Home screen or inside a story). These are the settings worth a look, in the order I would check them. Everything else can stay at its default.

| # | Where | What to check | Why |
|---|---|---|---|
| 1 | **AI & story** | **Which AI does what.** Pick the AI for the three jobs (narrator, recaps & helpers, pictures). Paste your API key if it says "needs a key". Click "Use it" on the recommendation if you are unsure. | Nothing works until a narrator is chosen. The app reads your hardware and recommends something sensible. |
| 2 | **Memory & cost → Spending limit** | Set a **per-day** (and/or per-month) dollar limit, and choose *Just warn me* or *Ask before every paid call*. | Online AIs bill by use. A limit stops a surprise bill. |
| 3 | **Memory & cost → Claude prompt caching** | Leave **caching on**. If you play in long sittings, pick the **1 hour** lifetime; if you take long breaks, tick **Keep warm**. | Caching makes long stories much cheaper with Claude. (Other services cache automatically.) |
| 4 | **AI & story → Content** | **Adult (NSFW) content** is **off** by default and is set per story. | Decides whether the narrator may write explicit scenes. Your AI provider's own rules still apply. |
| 5 | **Pictures** | Check which picture service you use and press **Test: draw a small picture** on it. Choose how many reference pictures to send (default 3). | Confirms pictures work before you need them. |
| 6 | **Backup** | Turn on **automatic backups** (e.g. every 3 days) and point the backup folder at **another drive** if you can. | Protects your stories if the disk fails. |
| 7 | **Local models** (only if you use Ollama or ComfyUI) | Decide whether the app should **load and unload local models automatically**, and after how many idle minutes ComfyUI should stop. | Frees graphics memory when nothing is using it. Off by default. |
| 8 | **💲 Usage → Prices** (only for ChatGPT, Gemini or Grok) | Add a price row for the model you use. | Claude prices are built in; other prices change too often to include, and without them their cost shows as unknown (and the spending limit can't count them). |
| 9 | **AI & story → Side panel** | Choose how the side panel decides what to show (default: leave it to the story prompt). | See [Side panel](#side-panel). |
| 10 | **Appearance** | Optional: background and text colours, and a scene backdrop for the sides of wide windows. | Comfort. |
| 11 | **Libraries** | Optional: switch on the **avatar library** (reuse player avatars across stories) or **reuse character art across stories**. | Off by default. |

Local-model owners should also glance at **Advanced → Context size** (how much text a local model reads at once; bigger remembers more but needs more video memory; 8192 is safe on 12 GB).

## Quick start (five minutes)

1. Start the app and open **⚙ Settings → AI & story**. Choose a narrator and paste your key. Press **Save**.
2. On the **Home** screen press **＋ New story**. Type a title and paste or write a story prompt (the world, characters, rules and writing style). Press **Create & play**.
3. Press **Begin the story** (or type your first move) and press **Send**.
4. Keep playing. The side panel, memory and pictures take care of themselves; the sections below explain each one.

You do not need to fill in anything else to play. The creator's other tabs (cast, prologue, endings…) are optional.

---

## Using the app

### The Home screen

The app opens here. Each story is a **card** with a cover picture and a short description; click it to play.

- **⋯ on a card:** set the cover (upload, or ✨ generate one from the story) and description (type or ✨ write it), open that story's **Backups**, **Export story…** (a story package, see below), or **Delete story**.
- **＋ New story:** opens the story creator.
- **Import a save:** bring over a game you already played elsewhere (see [Importing](#importing)).
- **📦 Import a story:** open a story package someone made (or you exported).
- **💲 Usage** and **⚙ Settings.** If you switch on the avatar library there is also **🧑 Avatars**.

### Creating a story

**＋ New story** opens a form with tabs. Only the title and prompt are required.

| Tab | What it is for |
|---|---|
| **Basics** | Title, genre, a short description for the Home card, and the **story prompt**. 🧪 *Test this prompt* plays one sample move and checks the reply for problems. |
| **Cast** | Premake characters: name, how they look, personality, bio, and pictures. The narrator is told about them every turn. ✨ suggests a cast from your prompt. Pictures: one file per picture; the file name becomes its mood (for example `smile 1.webp`). |
| **Story start** | A **prologue** (events leading up to the story), the **opening scene**, and **suggested first moves** shown under it. ✨ can draft each from your prompt. |
| **Trackers & endings** | The side panel the story suggests, an art style and setting, and **possible endings** (hidden notes for the narrator; see [Endings](#endings)). |
| **Media** | Cover, intro/webtoon panels, backdrop picture. |
| **Options** | Whether the app tracks state itself, automatic reply suggestions, adult content, and which AI runs this story. |

The ✨ buttons use the *recaps & helpers* AI. Nothing is saved until you press **Create & play**. **✎ Edit story** later opens the same tabs for the parts that apply to an existing story (its cast and media are managed in 🎭 Cast and on the Home card).

#### Importing

- **Import a save** (Home): for a game played elsewhere. Choose a **summary** (paste your notes of what happened) or the **whole story** (paste text or load a file; the AI you choose summarizes it in parts, and the last few turns can stay word for word if the text uses `PLAYER:` and `NARRATOR:` labels). You can paste a status panel to pick up exactly where you stopped.
- **📦 Import a story:** opens a **story package** (a .zip made with **⭳ Export** in a story, or ⋯ → *Export story…* on a Home card). It contains everything the author made: prompt, prologue, opening scene and first moves, cast with pictures, suggested trackers, endings, cover, intro panels and backdrop. It never contains turns, memory, your avatar, notes or API keys. After importing, the app asks whether to **Start from scratch** or **Import a save** into that story.

### Playing

**Header buttons** (inside a story): the model menu (the narrator for this story), 🏠 Home, 🎭 Cast, 🧠 Memory, 🏁 Endings (only when the story has endings), 🔍 Search, 👤 Player, ✎ Edit story, ⭳ Export, ⚙ Settings. On a narrow screen, ℹ shows the side panel.

**Under the story, next to the input box:**

| Button | What it does |
|---|---|
| **↶ Undo** | Removes your last move and the reply. |
| **↻ Retry** | Undoes the last reply and asks again. |
| **⚖ Compare** | Sends your next move to two AIs at once and lets you keep the reply you prefer. |
| **⚡ Quick** | Adds a one-off instruction to your move: "shorter", "slow down", "don't decide for me"… |
| **💡 Suggest replies** | Writes three possible moves for you. Pick one to copy it into the input box, edit it if you like, and send. They disappear when you pick one and reappear (freshly written) when you next scroll to the bottom; × stops that. |
| **🎨 Illustrate** | Draws a picture of the latest reply. |

**While a reply is written,** the page stays where your move starts, so you can read at your own pace; a bar above the input shows "Writing…" and then "✓ Finished writing" with a *Jump to the end* button.

**Hover over any turn** for its tools: 🔊 read aloud (uses your browser's own voices; set them under 🎭 Cast → Cast tools → Voices), ☆ bookmark, ✎ edit the text, ⑂ **branch** (copy the story up to your move so you can try a different choice), ↻▾ retry the last reply with a different AI, and … continue a reply that was cut off.

**🔍 Search** finds text in the story and lists your bookmarks.

### The side panel

The panel on the right shows what the story has established: the scene, you, and each character (status, a slider for how they feel about you, bonds…). Values change only when a reply causes it; changed values are marked and **hovering shows Before → After**. The panel's data is also given to the narrator before each reply so it stays consistent.

You choose how it decides what to show (**Settings → AI & story → Side panel**, per story, with a default for new stories):

- **Leave it to the prompt author:** shows what the story prompt's own status panel provides.
- **I choose the fields:** only the fields you tick (day, time, place, situation; your status, alignment, bonds, stats; each character's status, affection, bonds, location, thought).
  - **Your own trackers:** under the ticked fields, press **＋ Add a tracker**. Type a name (say *Hunger* or *Gold*), choose where it lives (**You**, **Scene**, or **Each character**) and how it looks: a **slider** (a gauge with a from/to range), a **number** (with an optional unit), a **status** (one label from a list you write) or plain **text**. A short note on what it means helps the AI. Not sure which type fits? Press ✨ and the helper AI suggests the type, range and meaning from the name. There is nothing to configure afterwards: after each reply the helper AI updates your trackers, fixes any value that doesn't fit (for example words in a number), and a tracker added mid-story is filled in from what has already happened. Your trackers travel with the story when you export it.
- **Let the AI decide:** the AI reads the story prompt, picks what is worth tracking (shown in settings), and updates it after each reply.

*Fill from the story so far* rebuilds the panel for an existing story.

### Memory

The narrator reads your story prompt, the **memory** (a summary of older events) and the most recent turns every time. When the history gets long (default: over 24 messages) the oldest turns are folded into the summary automatically.

**🧠 Memory** lets you:
- **Edit the summary** yourself (it is the only record of older events, so mistakes get repeated).
- Add **pinned facts**: corrections that override everything ("Mara is left-handed").
- **Rebuild** the summary from the story's turns.
- Open this story's **Backups**: the app saves a copy before every summary, memory edit, turn edit, restore and delete, and every 5 turns (the newest 40 are kept). Restore any of them.

### Cast, pictures and the player

**🎭 Cast** shows a card per character. Click one for three tabs: **Bio** (looks, personality, bio, and *Tell the narrator about them*), **Key events** (a log the app fills in after each reply; you can edit it) and **Images** (all their pictures, drawing, redrawing, uploading).

- **Pictures and moods:** each character can have several pictures named for a mood (`normal 1`, `smile 2`, `combat 3`…). The narrator is told which exist and shows the best match above that character's lines, so existing pictures are always used first. Only when no mood fits can it ask for a new one, and nothing is drawn unless you press **＋ Draw**.
- **Characters without pictures:** a banner offers to make them. *Review…* lets you edit the description, add notes, and draw 4 options to pick a face; then the app can draw a set of moods in the background.
- **Cast tools:** import a folder of portraits, find characters without art, suggest a cast from the prompt, **group characters** (extras such as guards or zombies show as one card), and **Voices** for reading aloud (all under *Cast tools* at the bottom of the Cast screen).
- **Relationships tab:** how each character feels about you over time.
- **👤 Player:** your character's sheet (name, gender, age, appearance, personality, background, abilities), your **notes** to the narrator (standing instructions), and the **Art style** (art direction, the story's *setting*, negative prompt, auto-illustrate). **✨ Generate** draws your avatar from the sheet.
- **Fitting the story's world:** every picture the app generates is told the story's *setting* (a short description of its world, written once from your prompt; editable under Player → Art style), so characters are dressed to suit it.
- **Avatar library** (optional): save several player avatars and reuse them in any story; each story uses one. **Reuse character art across stories** (optional): borrow a character's pictures from another story as a face reference; the new story's own art style still applies.

### Pictures and backdrops

Pictures are made by a separate picture AI (online, or ComfyUI on your computer). Claude cannot draw. Scene pictures appear when you press 🎨 (or automatically, if you turn on *auto-illustrate*). The picture service and its settings are under Settings → Pictures.

**Backdrop:** Settings → Appearance can show a picture from the story's setting or its latest scene on the **sides of a wide window**, with the centre keeping your colours. It never scrolls.

### Endings

A story can list **possible endings** (the creator's *Trackers & endings* tab). They are hidden notes: the narrator may drift toward one when your choices lead there, and never reveals them. **🏁 Endings** (spoilers!) lists them and lets you ask the story to work toward one.

### Choosing the AI for each story

Each story has three AI jobs: the **narrator** (writes the story, so quality matters most), **recaps & helpers** (summaries, key events, bios, picture prompts, suggestions: small jobs where a cheaper AI is fine) and **pictures**. Settings → AI & story shows what is available (the models your keys can use, local models, ComfyUI) and recommends for your computer. The model menu in the header changes the narrator for the current story.

### Costs and limits

**💲 Usage** shows tokens and **estimated** cost by story, job, model and day. Local models and ComfyUI cost nothing. Online prices are estimates from a price table (Claude built in; add others under *Prices*). The **spending limit** (Settings → Memory & cost) warns you from 80% and either just warns or asks before each paid call once reached (you can still send "this once"). Calls without a known price count as zero.

### Local models on demand

**Settings → Local models** (off by default) can load your Ollama model when you open a story and unload models nothing uses, and start ComfyUI when pictures are needed and stop it (or just free its memory) after it has been idle for the time you choose. If video memory is short when a local picture is about to be drawn, the text model is unloaded first. The tab shows what is running and has **Free memory now**.

### Backup and restore (everything)

**Settings → Backup** makes one .zip of all stories, pictures, avatars, usage log and settings (API keys left out unless you tick the box), by download or into a backup folder, optionally automatically every N days. **Restore** puts a backup's files back after saving a safety copy. This protects against a lost disk; the per-story backups (🧠 Memory → Backups) protect against bad edits.

### Phone and tablet

The layout adapts to small screens. To use it from another device on your home network, start the app with `./run-lan.sh`: it prints an address and a 6-digit **PIN** (other devices must enter it; this computer never needs it). Only do this on a network you trust.

---

## Settings reference

Settings has eight tabs. The Home screen and every story open the same window.

| Tab | Contains |
|---|---|
| **AI & story** | Which AI does what; adult content for this story; side panel mode; key-events log on/off; link to Usage. |
| **Memory & cost** | Spending limit; when to summarize and how much to keep word for word; Claude prompt caching, cache lifetime and keep-warm. |
| **Libraries** | Avatar library on/off; reuse character art across stories on/off. |
| **Pictures** | Reference pictures per scene; the mood lists drawn for a new character; the settings of each picture service (address, model, ComfyUI workflow) with a Test button. |
| **Appearance** | Background and text colours (presets, live preview, contrast check); scene backdrop on/off, strength, and generating one for the open story. |
| **Local models** | What is running now; automatic Ollama and ComfyUI management; ComfyUI start command; *Free memory now*. |
| **Backup** | Download or save a whole-app backup; automatic backups; restore. |
| **Advanced** | Creativity (temperature: how adventurous the wording is, mostly for local models); longest reply; context size (local models); Claude's thinking and effort; the list of AI providers. |

---

## Troubleshooting

| Problem | Try |
|---|---|
| The icon does nothing | Run `./launch.sh` in a terminal and read the message; check `data/server.log`. The first-time setup (`.venv`) must be done. |
| "needs a key" / no models listed | Paste the API key in Settings → AI & story, press **Save key** next to it, then *Re-scan*. Check the key belongs to that company. |
| A reply is cut off | Raise **Settings → Advanced → Longest reply**, or press **…** (continue) on the reply. With Claude, set *Effort* to Low so hidden thinking doesn't use the budget. |
| Pictures don't appear next to dialogue | The narrator must write portrait tags. Make sure the story prompt's *Images* section tells it to (see Technical notes), and that the character has pictures in 🎭 Cast. |
| Picture drawing fails | Settings → Pictures → Test. For ComfyUI, check it is running (Settings → Local models shows this). |
| A local model is very slow | It doesn't fit in video memory. Choose a smaller model, lower the context size, or turn on *Make room for pictures* / use online pictures. |
| The narrator keeps repeating a wrong fact | 🧠 Memory: fix the summary and add a **pinned fact**. |
| I changed something and want it back | 🧠 Memory → **Backups** (per story) or Settings → Backup → **Restore** (everything). |
| Spending limit says unknown prices | 💲 Usage → Prices: add a row for the model. |

---

## Technical notes

*(For people who want to look under the hood. You do not need these to play.)*

**Where things are.** `app/` is the server (Python, FastAPI); `static/` is the browser app; `examples/` holds a sample story prompt and a template; `workflows/` holds ComfyUI workflows; `data/` holds everything of yours (stories, pictures, avatars, settings, usage log, backups). Back up `data/` or use Settings → Backup. API keys you enter are stored in `data/config.json` in plain text: keep that folder private and never share or commit it.

**How a turn is built.** Each request is laid out so its start never changes: story prompt, your avatar, the author's cast notes and endings, the picture list and engine rules; then your notes, the prologue and the memory summary; then the history (append-only); and last, the live side-panel data attached to your newest message. This is what makes caching work: Claude marks cache breakpoints after the stable blocks and after the last reply (cached reads cost about 10% of normal; writes cost 25% more for the 5-minute lifetime, 100% more for 1 hour), Ollama reuses its own copy when the prompt start is identical, and other online services cache matching prefixes automatically. Changing the story prompt, cast notes, pictures list or content setting makes the next Claude turn rewrite the cache once. **Keep warm** sends a 1-token request shortly before the cache would expire. The line under each reply shows cached versus new tokens.

**Portrait tags.** The narrator writes `![](portrait:Name/mood)` on its own line above each line of dialogue; the app shows the matching picture. The app adds a PORTRAITS section to the instructions listing the available names and moods (characters with identical moods share one line). A story prompt that works well says, in its *Images* section: put the tag directly above every NPC dialogue line, spelled exactly as listed, mood lowercase with its number, and show it in the dialogue-format example.

**Status panels.** If the story prompt makes the narrator print a status panel (a fenced `Info` block with a `Characters──` section of rows like `Name:👤/❤️95(+1)/Location/"thought"`), the side panel can read it. Otherwise the state block or the AI fills the panel.

**ComfyUI (local pictures).** Install ComfyUI from github.com/comfyanonymous/ComfyUI, put an SDXL-class checkpoint in `models/checkpoints`, start it (it should listen on `127.0.0.1:8188`), and select it under Settings → Pictures. The built-in starter workflow is plain text-to-image. For consistent faces the project includes `workflows/comfyui-ipadapter-face.json`, a workflow with an IPAdapter face node that takes up to three reference pictures; it needs the IPAdapter custom node and its models installed in ComfyUI. Workflows use placeholders: `{{prompt}} {{negative}} {{seed}} {{width}} {{height}} {{model}}` and `{{ref1}}…{{ref3}}` for reference pictures. Anime SDXL models follow short tag-style prompts best, so each picture service has a *Prompt style* (Tags or Sentences). Scene descriptions are kept safe for work.

**Portrait packs.** 🎭 Cast → *Import portraits…* reads a folder with one sub-folder per character and pictures named for moods (`Mara/Angry 1.webp`). It shows a preview first, copies (never moves) files, and skips pictures already imported. Folders named like `Sub-NPCS` or `Extras` become one-picture characters; folders like `Intro` or `Gallery` are skipped.

**Optional, halve the memory a local model's context uses** (run once):

```bash
sudo systemctl edit ollama   # add under [Service]: Environment="OLLAMA_FLASH_ATTENTION=1" and Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
sudo systemctl restart ollama
```

**Adding another AI service.** Anything with an OpenAI-compatible API (OpenRouter, LM Studio, DeepSeek…) can be added under Settings → Advanced → Providers. Provider code lives in `app/providers.py`.

## License and privacy

Released under the MIT License (see `LICENSE`). The app has no accounts and sends no usage data anywhere. Text is sent only to the AI service you choose (and picture prompts to the picture service you choose); with local models nothing leaves your computer.
