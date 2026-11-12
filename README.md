# AI Storyteller

A private, browser-based game where an AI plays the narrator. You write (or import) a **story prompt**: the world, the characters, the rules and the writing style. Each turn you type what your character does, and the AI narrates what happens next, remembering the story as it grows. It can also keep a **cast** with portraits, track stats in a **side panel**, draw pictures, and read the story aloud.

Everything runs on your own computer. Your stories, pictures and settings are stored in the `data` folder next to the app. The only things that leave your computer are the text sent to the AI service you choose (Claude, ChatGPT, Gemini or Grok), if you use one. Local models (Ollama, ComfyUI) send nothing anywhere.

**Contents:** [What it is and isn't](#what-this-app-is-and-what-it-is-not) · [Download and install](#download-and-install) · [What you need](#what-you-need) · [Starting and stopping](#starting-and-stopping) · [Before your first story: settings to check](#before-your-first-story-settings-to-check) · [Quick start](#quick-start-five-minutes) · [Using the app](#using-the-app) · [Settings reference](#settings-reference) · [Troubleshooting](#troubleshooting) · [Technical notes](#technical-notes)

---

## What this app is, and what it is not

**It is** a program you run on your own computer. It gives you the play screen, memory, side panel, cast and picture tools, and connects to an AI that you choose to write the story. You open it in your web browser, but nothing is hosted online: stories, pictures and settings stay in the `data` folder next to the app.

**It does not:**
- **Include an AI.** The writing comes from an AI you connect yourself (an online service with its own account and billing, or a free local one you install). Without one, the app cannot write anything.
- **Include stories, characters or pictures of its own.** It ships with only two small example prompts. You write, paste or import your own story prompt.
- **Cost anything itself.** The app is free. Online AIs and online picture services charge you directly, per use.
- **Decide what content is allowed.** Your chosen AI service has its own rules and may refuse or filter some content. The app's adult setting only lets the narrator try; it can't override a provider's rules. Content involving anyone under 18 is never written.
- **Work as a multiplayer or online game.** It is for one player. You can open it from your phone or tablet on your home network, but it is not a website for others.
- **Run on macOS or phones.** It runs on **Linux (Ubuntu, well tested)** and **Windows 10/11 (new, not yet tested on a real Windows machine)**. The computer has to stay on while you play.
- **Guarantee quality.** Story quality, how well rules and stats are followed, and picture quality depend on the AI and picture model you pick. Small local models are noticeably weaker than the big online ones.
- **Back up for you automatically** unless you turn it on (Settings → Backup).

**Before you can properly start, you need:**

| You need | Required? | Notes |
|---|---|---|
| A **Linux (Ubuntu) or Windows 10/11** computer with **Python 3.10+** and a web browser | Yes | The installer checks Python for you. On Windows, install Python from python.org first and tick *Add python.exe to PATH*. |
| **One AI to narrate**: an online AI (account, payment method, API key) **or** a local AI through Ollama | Yes | Online: a few cents per turn, billed separately from any chat subscription. Local: free, but needs a capable graphics card (about 12 GB of video memory for a good one). |
| **A story prompt** | Yes | Paste the example in `examples/`, or write or import your own. |
| A spending limit set in Settings | Strongly advised | Prevents a surprise bill with online AIs. |
| **A picture maker** (online service, or ComfyUI on your own graphics card) | No | Only for drawn portraits and scene pictures. Stories play fine without it. |
| Portraits, trackers, quests, voices | No | Optional extras, all off or empty until you set them up. |

---

## Download and install

1. **Download:** on the project's GitHub page press the green **Code** button → **Download ZIP**, then unzip it. (Or run `git clone https://github.com/deeboy814/AI-Storyteller.git`.)
   **On Windows, do this instead of steps 2 and 3:** open the folder and double-click **`install.bat`** (needs [Python 3.10+](https://www.python.org/downloads/) installed with *Add python.exe to PATH* ticked). It installs the libraries into the folder (nothing system-wide) and puts an **AI Storyteller** shortcut on your Desktop and Start menu. Then double-click that shortcut (or `launch.bat`). If Windows SmartScreen warns about the scripts, choose *More info → Run anyway*. The rest of this README works the same on Windows; where it shows `./something.sh`, use the matching `.bat` file instead.
2. **Linux: open a terminal in the unzipped folder** (right-click inside it → *Open in Terminal*) and run:

   ```bash
   ./install.sh
   ```

   It checks Python, installs the app's libraries into the folder (nothing system-wide), adds an **AI Storyteller** icon to your Desktop and application menu, and offers to install Ollama (free local models). Answer the questions, or run `./install.sh --yes` to accept the optional steps. It is safe to run again.
3. **Start the app:** double-click **AI Storyteller** (or run `./launch.sh`). Your browser opens the app at `http://127.0.0.1:8000`. Everything stays on your computer.
4. Open **⚙ Settings** and follow [Before your first story](#before-your-first-story-settings-to-check).

It runs on Linux (tested on Ubuntu) and Windows 10/11 (the Windows scripts have not yet been tried on a real Windows machine; if something fails, see Troubleshooting and please report it). It needs **Python 3.10 or newer**. To remove the desktop icon later run `./uninstall.sh` (Windows: `uninstall.bat`); your stories are not touched.

Want to see a finished story package? Import `examples/Example Story Package - The Lantern Market.zip` with **📦 Import a story**: a complete, playable example (prompt, opening, cast with pictures, a species, places, trackers, endings and a starter character) whose prompt carries `GUIDE:` notes explaining each part; `examples/lantern-market/HOW-TO-BUILD-A-STORY-PACKAGE.md` walks through building your own. Or paste `examples/the-lighthouse-keepers.md` (a small original story prompt) into **New story**; `examples/story-prompt-template.md` explains how to write your own.

## What you need

- A computer running **Ubuntu Linux** or **Windows 10/11** (both are covered below).
- **At least one AI to narrate**, either:
  - an **online AI** (Claude, ChatGPT, Gemini or Grok): you create an account with the company, add a payment method and get an *API key* (a long password for apps). Online AIs are the easiest and write the best stories, but they cost money per use (typically a few cents per turn). They bill separately from any chat subscription you may have. Where to get a key:
    - Claude: console.anthropic.com → API Keys
    - ChatGPT (OpenAI): platform.openai.com → API keys
    - Gemini (Google): aistudio.google.com → Get API key
    - Grok (xAI): console.x.ai → API keys
    - **Tested and working:** Claude, Gemini and Grok have each been tried in the app for story writing, and Gemini and Grok for pictures too (version 1.6.1). Gemini needs a Google account with billing switched on for its picture models and its Pro models; on Google's free tier those answer with a quota error, and Google no longer offers its older 2.5 models to new keys, so pick a newer one from the model list. ChatGPT (OpenAI) is supported the same way but has not been tried as recently.
  - or a **local AI** through *Ollama* (free, private, runs on your graphics card). Quality depends on your graphics card: 12 GB of video memory runs a mid-sized model well; 8 GB or less is limited.
- **Optional, for pictures:** either an online picture service (Gemini, OpenAI or Grok, billed per picture) or *ComfyUI* on your own graphics card (free; needs about 6 GB of video memory or more).

## Starting and stopping

Double-click **AI Storyteller** on your Desktop (also in the application menu). It starts the app in the background if needed and opens it in your browser. To close it, press **⏻ Quit** on the Home screen (🏠 from inside a story) and confirm; your stories are saved as you play. The right-click menu on the icon also offers **Stop AI Storyteller**. Without the icon: `./launch.sh` starts it and opens the browser, `./run.sh` starts it in a terminal, `./stop.sh` stops it. If something goes wrong, the log is `data/server.log`.

**Windows:** use the Desktop shortcut (or `launch.bat`) to start, **⏻ Quit** or `stop.bat` to close, `run.bat` to run in a window, `run-lan.bat` for phones. The log is `data\server.log`. For local models on Windows, install Ollama from ollama.com/download and run `ollama pull mistral-nemo` in a Command Prompt.

**Optional local models on Linux** (if you skipped them in the installer):

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
| 4 | **AI & story → Content** | **Adult (NSFW) content** is **off** by default and is set per story. | Decides whether the narrator may write explicit scenes **and** whether scene pictures and portraits may show adult content (adults only; covers and backdrops always stay safe for work). Online picture services (Gemini, OpenAI, Grok) usually refuse explicit images, so for those pictures use a local ComfyUI model. Every provider's own rules still apply. |
| 5 | **🖼 Art → Picture makers** (inside a story, or 🖼 Picture makers on the Home screen) | Check which picture service you use and press **Test: draw a small picture** on it. Choose how many reference pictures to send (default 3). | Confirms pictures work before you need them. |
| 6 | **Backup** | Turn on **automatic backups** (e.g. every 3 days) and point the backup folder at **another drive** if you can. | Protects your stories if the disk fails. |
| 7 | **Local models** (only if you use Ollama or ComfyUI) | Decide whether the app should **load and unload local models automatically**, and after how many idle minutes ComfyUI should stop. | Frees graphics memory when nothing is using it. Off by default. |
| 8 | **💲 Usage → Prices** (only for ChatGPT, Gemini or Grok) | Add a price row for the model you use. | Claude prices are built in; other prices change too often to include, and without them their cost shows as unknown (and the spending limit can't count them). |
| 9 | **AI & story → Side panel** | Choose how the side panel decides what to show (default: leave it to the story prompt). | See [Side panel](#side-panel). |
| 10 | **Appearance** | Optional: background and text colours, two-tone story text (dim narration, bright dialogue) and showing a backdrop on the sides of wide windows. | Comfort. |
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
- **Free tidy and consistency checks (no AI):** with *Tidy each reply's status line* on (✏️ Edit story → Options), a doubled status line is reduced to one and a misplaced one is moved to the end when the prompt says it is the last line (noted under the reply). The free check also flags a character the side panel says is dead speaking, a name that looks like a misspelling of a cast member, and an eye or hair colour that contradicts the character's card. **Art → Style & options** has two free options that skip an AI call: *Build scene picture prompts in the app* (from the reply's portrait tags, place and most active sentence) and, for the Right-now card, *Fill the card from the narrator's portrait tags only*. The automatic background no longer asks the AI when a place tag, the Place tracker or the reply's opening words name a known location, and a reply that only mentions the player makes no key-event AI call.
- **Free checks and bookkeeping (no AI, no cost):** after every reply the app checks the format and numbers itself when your story prompt shows a status line (printed twice or not last, labels missing, Day, XP or Lives moving the wrong way, XP past the next level, stat points changed without you spending them, a number change with no status line) and shows flags under the reply with a one-click fix (switch it off under ✏️ Edit story → Options). Notifications such as *Level 3 reached*, *Life lost*, *Feat recorded*, *Merit +2* and *XP +80* become **key events on your character's card** automatically, and the ⚡ bar offers **suggested moves** built from the scene (talk to whoever is here, go to a Place, your active quest, spend points). **Import** also reads JSON chat exports (ChatGPT, Claude, SillyTavern, OpenAI-style message lists) and ChatGPT-style "You said / ChatGPT said" transcripts, and the Cast screen offers to **merge characters that are probably one person** ("Ann" and "Ann Marsh", a name with and without a title, near-identical spellings).
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
| **Options** | Whether the app tracks state itself, automatic reply suggestions, adult content, **"Let the narrator decide some of my character's routine actions"** (the app's own rule says only you decide what your character does; this relaxes it so the story prompt can let the narrator write routine, in-character actions from your backstory, stats, skills, quests and the story so far, while still obeying what you write; never points or big decisions unless the prompt says), and which AI runs this story. |

The ✨ buttons use the *recaps & helpers* AI. Nothing is saved until you press **Create & play**. **✎ Edit story** later opens the same tabs for the parts that apply to an existing story (its cast and media are managed in 🎭 Cast and on the Home card).

#### Importing

- **Restart a story from the beginning** (Home → the story's ⋯ → *↺ Restart from the beginning…*): makes a separate copy that starts again at the beginning, named as you like. It keeps the setup (story prompt, prologue, opening, options, trackers, art settings, places, species, lore, story beats, your character, and the cast with their pictures) and drops what came from playing (turns, memory, side-panel values, key events, pinned facts, bookmarks, and characters the story introduced later). Your current game is untouched. It also works on **imported saves, including ones imported from text or a summary**: only the setup carries over, not the imported history, and for a story prompt the AI wrote from your text its *Where the story stands* part is left out so the copy does not start from where you ended (a prompt you wrote yourself is never edited).
- **Import a save** (Home): for a game played elsewhere. Choose a **summary** (paste your notes of what happened) or the **whole story** (paste text or load a file; the AI you choose summarizes it in parts, and the last few turns stay word for word: labelled text (`PLAYER:` / `NARRATOR:`, also You / Assistant, Human / AI, Me / GM, with or without markdown bold) keeps its last turns, and plain prose keeps its last few paragraphs as the scene in progress). You can paste a status panel to pick up exactly where you stopped. **No story prompt?** Leave the prompt empty (and pick *Paste a prompt below* in the list): the AI writes one from your summary or text (the world, the characters and how they speak, the open threads, the writing style it saw, nothing invented), plus a title, a subtitle and the player's name when the text gives it. Read it under ✏️ Edit story, because the narrator follows it every turn. `{{user}}` in prompts from other apps works too.
- **📦 Import a story:** opens a **story package** (a .zip made with **⭳ Export** in a story, or ⋯ → *Export story…* on a Home card). It contains everything the author made: prompt, prologue, opening scene and first moves, cast with pictures, suggested trackers, endings, cover, intro panels and backdrop. It never contains turns, memory, your avatar, notes or API keys. After importing, the app asks whether to **Start from scratch** or **Import a save** into that story.

### Playing

**Header buttons** (inside a story): the model menu (the narrator for this story), 🏠 Home, 🖼 Art, 🎭 Cast, 📅 Story beats, 🧠 Memory, 🏁 Endings (only when the story has endings), 🔍 Search, 👤 Player, ✎ Edit story, ⭳ Export & backups, ⚙ Settings. On a narrow screen, ℹ shows the side panel.

**Under the story, next to the input box:**

| Button | What it does |
|---|---|
| **↶ Undo** | Removes your last move and the reply. |
| **↻ Retry** | Undoes the last reply and asks again. |
| **⚖ Compare** | Sends your next move to two AIs at once and lets you keep the reply you prefer. |
| **▶️ Continue** | The narrator carries the story on by itself from the prompt, characters, world lore and the last few turns, and stops where you need to act. Anything you typed is used as a hint. |
| **🎲 Check** (in ⚡) | The app rolls a d20 + the stat's bonus (stat − 5; above 10 it eases off, +3 per doubling, so high-level stats still matter) against a difficulty (easy 8, medium 12, hard 16, very hard 20, heroic 24) and sends your move with the result; the narrator must narrate that outcome and cannot change it. The same window sets **automatic rolls**: if your story prompt mentions rolls, checks or dice, the app rolls by itself when your move is a risky action that fits one of your stats (dodge → Reflexes, shoot → Aiming, lift → Strength…); otherwise only when you press 🎲. It can also be set to never roll. A roll is shown under your move. |
| **⚡ Quick** | Adds a one-off instruction to your move: "shorter", "slow down", "don't decide for me"… It also holds the story commands: **▶ Continue**, **⚔ Continue the fight** (the narrator keeps a battle going with initiative; tick *narrator may act for me* to let it choose your character's actions too) and, only in stories with Adult content on, **🔞 Full adult scene** (a fuller instruction for narrator models that fade to black or skip intimate scenes; press it to attach it to your next move, or tick *every move*). Commands are sent with that request only; Retry sends them again. |
| **💡 Suggest replies** | Writes three possible moves for you. Pick one to copy it into the input box, edit it if you like, and send. They disappear when you pick one and reappear (freshly written) when you next scroll to the bottom; × stops that. |
| **🎨 Illustrate** | Draws a picture of the latest reply. |

**While a reply is written,** the page stays where your move starts, so you can read at your own pace; a bar above the input shows "Writing…" and then "✓ Finished writing" with a *Jump to the end* button.

**Hover over any turn** for its tools: 🔊 read aloud (uses your browser's own voices, so quality depends on what is installed: on Linux the default engine, espeak-ng, is robotic but clear; set voices under 🎭 Cast → Cast tools → Voices), ☆ bookmark, ✎ edit the text, ⑂ **branch** (copy the story up to your move so you can try a different choice), ↻▾ retry the last reply with a different AI, and … continue a reply that was cut off.

**🔍 Search** finds text in the story and lists your bookmarks.

### The side panel

The panel on the right shows what the story has established: the scene, you, and each character (status, a slider for how they feel about you, bonds…). Values change only when a reply causes it; changed values are marked and **hovering shows Before → After**. The panel's data is also given to the narrator before each reply so it stays consistent.

You choose how it decides what to show (**Settings → AI & story → Side panel**, per story, with a default for new stories):

- **Leave it to the prompt author:** shows what the story prompt's own status panel provides.
- **I choose the fields:** only the fields you tick (day, time, place, situation; your status, alignment, bonds, stats; each character's status, affection, bonds, location, thought).
  - **Your own trackers:** under the ticked fields, press **＋ Add a tracker**. Type a name (say *Hunger* or *Gold*), choose where it lives (**You**, **Scene**, or **Each character**) and how it looks: a **slider** (a gauge with a from/to range), a **number** (with an optional unit), a **status** (one label from a list you write) or plain **text**. A short note on what it means helps the AI. Not sure which type fits? Press ✨ and the helper AI suggests the type, range and meaning from the name. There is nothing to configure afterwards: after each reply the helper AI updates your trackers, fixes any value that doesn't fit (for example words in a number), and a tracker added mid-story is filled in from what has already happened. Your trackers travel with the story when you export it.

  **World clock (optional, per story):** ✏️ Edit story → Options → *World clock*: *Let the story prompt decide* (on when the prompt talks about a clock or times of day, or the story has a Time value or tracker), *On* or *Off*, with the time a day starts at and the minutes each reply moves time on by. The app keeps the in-world time, shows it in the side panel's Scene section (click it to set the time by hand), resets it when the Day changes, adopts a Time the narrator prints itself (for a time skip), and tells the narrator the time each turn so light, meals and curfews stop drifting.

  **Quest deadlines:** a quest notification that says *Deadline: Day 9* is remembered; the quest shows a ⏰ badge, the narrator is told when it is due today and when it is overdue (to resolve it as failed unless the story shows it done), and it is forgotten when the quest ends.

  **Points you spend (optional):** for a **Number** tracker (say *Points*), *spend points on* takes a list of stats and skills (for example `Strength, Stamina`) and an optional *max*. The side panel then shows a **⭐ N points to spend** box and **+** buttons next to those stats; the app does the arithmetic, and the narrator is told the final numbers with your next move and **never assigns points itself** (if it prints the old numbers, the app puts the right ones back). A new game opens an **assign window** before the first move, with starting values and points read from your avatar sheet (for example "Strength 8, Reflexes 6… Free points to assign at the start: 5"); the window can **add a skill** (for example one your class gives) that points can be spent on, and you can take back points you spent since your last move. Retry sends the same spend again.

  **Turn counter (optional):** for a **Number** tracker, *counts replies since [another tracker] changes* makes the app count the narrator's replies since that tracker (for example **Day**) last changed, so you get a *Day turn* that never depends on the narrator counting; it starts again at 1 on a new day. The narrator is told the number each turn (so a story prompt can say "a day lasts 3 to 12 turns"), and the app overrides any number the narrator prints for it.

  **Quest log (optional, any story):** choose the type **Quest log (tab)** for a tracker and the side panel gains a **Quests** tab. Each quest is one line with its progress; click it to open its objectives, reward and status. When the story completes a quest it stays, marked completed with its reward, for one turn, then clears. Tell the narrator in your story prompt to announce the completion and hand over the rewards in that reply.

  **Panel tabs:** when the panel has more than one kind of content it shows tabs (**Info**, **Characters**, **Quests**). The ▾ button minimizes the panel to just the tab strip; click it again, or any tab, to open it. The panel scrolls when the content is long.
- **Let the AI decide:** the AI reads the story prompt, picks what is worth tracking (shown in settings), and updates it after each reply.

*Re-read the story and rebuild* (Settings → AI & story → Side panel) forgets the panel's values for the story's own trackers and reads them again from the whole story, which corrects a panel that went wrong.

### Memory

The narrator reads your story prompt, the **memory** (a summary of older events) and the most recent turns every time. When the history gets long (default: over 24 messages) the oldest turns are folded into the summary automatically. The summary is laid out as a short status paragraph plus a numbered list of events in order; an update only writes the new entries and the app appends them (it does not rewrite the whole summary each time, which is faster and cheaper). With Claude's prompt cache warm, an update may wait a few extra turns, since changing the summary makes the next reply pay to write the cache again.

**Character memory** (Settings → Memory & cost, on by default) keeps each character's story with that character: their key events are saved on their card (see Cast) and sent to the narrator only while they are in the scene (named in your latest move or the narrator's reply). That lets the shared summary stay short (plot, places, items, open threads) and the narrator still know who did what. Pick how much of each character's history it sees (Small, Medium, Large). When a character's log gets long, the helper AI condenses the older entries by itself; your own entries are never touched. The first time an old-style summary is updated, its "Key characters" part moves onto the character cards (a backup is saved first).

**📚 Lore** (🧠 Memory → 📚 Lore, or from 📅 Story beats) keeps facts about your world (a place, a faction, a rule, a character's history). An entry is sent to the narrator, hidden, **only when one of its trigger words** appears in your move or the last two turns, or always if you tick *Always send*; at most 8 entries and about 3,500 characters go with one move, most important first. No AI is involved. A *Test* box shows which entries a sentence would trigger. Entries are saved with the story and **included in story packages** (a game updated from a package gets only the entries it does not have yet).

**✨ Fill the whole sheet (character page → Bio):** reads the story so far and suggests every box of a character's sheet (how they look, personality, bio, names and forms of address, also called, species), and can rebuild their key-event log. **Do not use it early in a story:** the AI only knows what the story has shown, so early on it guesses, and a saved guess becomes a fact the narrator repeats every turn. The dialog shows how many replies there are and how often the character appears, warns (and needs a confirmation) below 15 replies or 5 appearances, lets you fill only the empty boxes or every box, and shows each suggestion beside what is there now. Nothing is saved until you press Apply on the boxes you ticked.

**📅 Story beats** is the story's own schedule of key events (a day-by-day calendar, an exam, an attack, a reveal). The first time a story is opened, the helper AI reads the story prompt for them (📅 → *Read the story prompt* reads it again), and the app keeps them. Each has a trigger tied to the side panel ("when **Day** reaches 7"), to your move count ("at move 12") or "on my next move". When it is due, the app adds it **invisibly to your next move** so the narrator works it into the story at a fitting moment, and keeps reminding it for a few moves (you choose how many); nothing is added to the story text or to what you type. It also tells the narrator, hidden, what comes next so the story leads toward it and does not drift off the plan (switch that off if you prefer). It is on by default; every event is **hidden behind a blur** until you tap it or press *Show all*. You can edit, skip, delete or add your own events (choose a side-panel value, a move number, or the next move), and *Send again when due* replays one. If a calendar jumps ahead, at most two events go out per move. Beats travel in story packages (never which have gone out), and the Continuity check also reads them to flag a story that has drifted from its plan. Reading the prompt costs one small helper call.

**Command buttons.** A story can have its own row of buttons above the input box (✏ Edit story → Options → *Command buttons*, one per line as `Label | what it sends`). A button sends its text as your move, or with `@edit` puts it in the box for you to finish, or, written as `Label | @points`, opens the stat-points window (the app does the arithmetic, no AI call). A story package carries its buttons, so a game can offer *Status*, *Shop* or *Assign points*.

**Choices in a story (branching).** A story can track a choice with a status tracker (for example *Route: home first / explored first*) and tie a story beat to its value (*when Route is home first*): each beat then fires once, for the path the player actually took. A story's own trackers can also have a start value.

**🧠 Memory** lets you:
- **Edit the summary** yourself (it is the only record of older events, so mistakes get repeated).
- Add **pinned facts**: corrections that override everything ("Mara is left-handed").
- Run a **🔎 Continuity check**: the AI reads every turn (plus the rules, cast cards, pinned facts and summary) and lists real contradictions: looks, ages, places, timeline, items, injuries, deaths, who knows what, broken rules. Deliberate changes are kept. Each problem comes with the smallest repair; tick the ones you want, and the app makes them (old wording stays in the turn's edit history, a backup is saved first), pins the corrected fact, and checks again. Problems with no safe repair are left for you. It reads the whole story, so on a paid AI it costs real money (the dialog shows the size first).
- **Rebuild** the summary from the story's turns.
- Open this story's **Backups**: the app saves a copy before every summary, memory edit, turn edit, restore and delete, and every 5 turns (the newest 40 are kept). Restore any of them.

### Cast, pictures and the player

**🎭 Cast** shows a card per character. Click one for three tabs: **Bio** (looks, personality, bio, and *Tell the narrator about them*), **Key events** (a log the app fills in after each reply and condenses when it grows long; you can edit it; it is what the narrator is reminded of when they are in the scene) and **Images** (all their pictures, drawing, redrawing, uploading, and their *picture tags* for tag-style picture models, which you can read, edit or have rewritten).

- **Pictures and moods:** each character can have several pictures named for a mood (`normal 1`, `smile 2`, `combat 3`…). The narrator is told which exist and shows the best match above that character's lines, so existing pictures are always used first. Only when no mood fits can it ask for a new one, and nothing is drawn unless you press **＋ Draw**.
- **Characters without pictures:** a banner offers to make them. *Review…* lets you edit the description, add notes, and draw 4 options to pick a face; then the app can draw a set of moods in the background.
- **Cast tools:** import a folder of portraits, find characters without art, suggest a cast from the prompt, **group characters** (extras such as guards or zombies show as one card), and **Voices** for reading aloud (all under *Cast tools* at the bottom of the Cast screen).
- **Species and picture makers:** a character's page has *Species* (empty = human) and *Picture maker*. The first picture drawn for a new species becomes its reference, so later members of that species share its look but keep their own face. For non-human characters choose **FLUX.2 klein with reference pictures** (the same model as the optional backgrounds maker); other ComfyUI checkpoints draw them from text only. Species traits and their default maker are under 🖼 Art → Characters → *Species & makers…*.
- **Other names and private names:** a character's page has *Also called* (so a nickname counts as the same character everywhere, and a duplicate can be merged into the real character) and *Names and forms of address* (which names or titles each person uses for them, and who may; sent to the narrator every turn).
- **Relationships tab:** how each character feels about you over time.
- **👤 Player:** your character's sheet (name, gender, age, appearance, personality, background, abilities), your **notes** to the narrator (standing instructions), and a **Draw 4 options** button that draws your avatar from the sheet (you pick one). Everything else about pictures lives in **🖼 Art** (below).
- **Fitting the story's world:** every picture the app generates is told the story's *setting* (a short description of its world, written once from your prompt; editable under 🖼 Art → Style & options), so characters are dressed to suit it.
- **🖼 Art** (header): every picture tool of the open story in one place. *Overview* shows each kind of picture with its status and opens its tool: characters (Cast & pictures, art for new characters, gallery), your avatar, **Places** (fixed pictures of rooms the story returns to; the narrator shows them with a tag), **Backgrounds**, the Home-card **cover**, intro panels, scene pictures and the picture settings. *Style & options* holds the art direction, the setting, the negative prompt and every picture switch (auto-illustrate, a scene picture most turns and whether scene pictures show under the reply, as a banner above or below the narration, or in the side panel's "right now" card, exact moods, new-character art, automatic background and its dramatic-moment variants, the side panel's "right now" card). *Backgrounds* draws, uploads or removes the story's backdrop and holds the switches for the **automatic background** (the helper AI says where each scene is; a Places picture, an earlier background or a newly drawn one is shown) and **dramatic-moment backgrounds** (alarm, fire, battle…). *Picture makers* holds the picture services, the mood lists and the optional separate maker for backgrounds and places. Covers, avatars and Places pictures all draw four options to choose from. Every screen opened from the Art menu has a **← Art menu** button to come back. Other switches in *Style & options*: **extended mood set** (new characters get the longer set of moods, and from the second turn on the narrator is reminded to use the whole range instead of mostly normal and smile) and **ask for exact moods**.
- **⭳ Export & backups:** a text file, an illustrated page, a story package, this story's automatic backups, and a one-click whole-app backup.
- **Avatar library** (optional): save several player avatars and reuse them in any story; each story uses one. **Reuse character art across stories** (optional): borrow a character's pictures from another story as a face reference; the new story's own art style still applies.

### Pictures and backdrops

Pictures are made by a separate picture AI (online, or ComfyUI on your computer). Claude cannot draw. Scene pictures appear when you press 🎨 (or automatically, if you turn on *auto-illustrate*). The picture service and its settings are under 🖼 Art → Picture makers.

**Backdrop:** Settings → Appearance switches on showing a picture on the **sides of a wide window**, with the centre keeping your colours; it never scrolls. 🖼 Art → Backgrounds draws it from the story's setting or the latest scene, lets you upload one, and can have it follow the scene automatically. A second, separate picture maker for backgrounds and places (for example the free **FLUX.2 klein** model in ComfyUI, which draws scenery well; it is a download of about 12.5 GB) can be switched on under 🖼 Art → Picture makers; characters, scene pictures and covers keep using the maker you chose.

**Scene pictures** can show under the reply, as a wide **banner above or below the narration**, or in the side panel's **Right now** card, which shows who you are talking to (picture for their current mood, what they are doing and wearing) in place of your avatar. **Places** (🖼 Art → Places) are fixed pictures of rooms the story returns to again and again; the narrator shows one with a tag when a scene starts there.

### Endings

A story can list **possible endings** (the creator's *Trackers & endings* tab). They are hidden notes: the narrator may drift toward one when your choices lead there, and never reveals them. **🏁 Endings** (spoilers!) lists them and lets you ask the story to work toward one.

### Choosing the AI for each story

Each story has up to four AI jobs: the **narrator** (writes the story, so quality matters most), **recaps & helpers** (summaries, bios, condensing: jobs where a cheaper AI is fine), an optional **small helper** (the short jobs after every reply: key events, new characters, side panel, the background place check, suggested replies and picture prompts; a cheap or local model is fine, and "same as recaps & helpers" is the default) and **pictures** (chosen under 🖼 Art → Picture makers). Settings → AI & story shows what is available (the models your keys can use, local models, ComfyUI) and recommends for your computer. The model menu in the header changes the narrator for the current story.

### Costs and limits

**💲 Usage** shows tokens and **estimated** cost by story, job, model and day. Local models and ComfyUI cost nothing. Online prices are estimates from a price table (Claude built in; add others under *Prices*). The **spending limit** (Settings → Memory & cost) warns you from 80% and either just warns or asks before each paid call once reached (you can still send "this once"). Calls without a known price count as zero.

### Local models on demand

**Settings → Local models** (off by default) can load your Ollama model when you open a story and unload models nothing uses, and start ComfyUI when pictures are needed and stop it (or just free its memory) after it has been idle for the time you choose. If video memory is short when a local picture is about to be drawn, the text model is unloaded first. The tab shows what is running and has **Free memory now**.

### Backup and restore (everything)

**Settings → Backup** makes one .zip of all stories, pictures, avatars, usage log and settings (API keys left out unless you tick the box), by download or into a backup folder, optionally automatically every N days. **Restore** puts a backup's files back after saving a safety copy. This protects against a lost disk; the per-story backups (🧠 Memory → Backups) protect against bad edits.

### Phone and tablet

The layout adapts to small screens. To play from a phone or tablet on your home network, turn on **Settings → Phone & tablet** and restart: the tab shows the address to open on the phone and a 6-digit **PIN** (other devices enter it once; this computer never needs it). Only devices on your home network are let in, and the phone talks only to this computer. If your computer has a firewall (Ubuntu's `ufw`), allow your home network once, as shown in that tab. (`./run-lan.sh` still works for a one-off session.) A device that has entered the PIN is **remembered**, also after a restart (Settings → Phone & tablet lists them with a *Forget* button). On a phone the header folds into a slim row with a ☰ menu, and a small tab on the right edge (or a swipe from the edge) opens the status panel.

**Tested on a phone:** checked on a real Android phone over a home network and at phone width in a desktop browser: all 38 screens and dialogs open without sideways overflow or script errors, the phone loads nothing from the internet, and a phone cannot read the PIN, close the app or make a bug report. iPhone, iPad and Windows have not been tested.

---

## Settings reference

Settings has eight tabs. The Home screen and every story open the same window.

| Tab | Contains |
|---|---|
| **AI & story** | Which AI narrates, helps and (optionally) does the small jobs; adult content for this story; side panel mode; key-events log on/off; link to Usage. |
| **Memory & cost** | Spending limit; when to summarize and how much to keep word for word; character memory and its size; Claude prompt caching, cache lifetime and keep-warm. |
| **Libraries** | Avatar library on/off; reuse character art across stories on/off. |
| **Appearance** | Background and text colours (presets, live preview, contrast check); two-tone story text and how dim the narration is; backdrop on/off and strength (drawing, uploading and automatic changing are under 🖼 Art → Backgrounds). |
| **Local models** | What is running now; automatic Ollama and ComfyUI management; ComfyUI start command; *Free memory now*. |
| **Backup** | Download or save a whole-app backup; automatic backups; restore. |
| **Advanced** | Creativity (temperature: how adventurous the wording is, mostly for local models); longest reply; context size (local models); Claude's thinking and effort; the list of AI providers. |

---

## Troubleshooting

**Report a bug.** *Home → 🐞 Report a bug* (or *Settings → Libraries & backup → Report a bug*) builds **one .zip file** to attach to a problem report on GitHub (Issues), together with your own explanation. You choose the game that went wrong and describe what happened, and the app captures how far it got (counts, settings, side-panel state, the recent turns with the app's own data on them, memory, story beats, lore, cast), **the problems it can find by itself** (turns that do not alternate, memory covering more turns than exist, missing picture files, a story model whose service has no key, format slips in the last replies, services that do not answer, recent errors and failed requests, Python tracebacks in the log), the errors the page noticed, and your settings and hardware. **Personal data is removed before anything is written:** API keys and tokens, your PIN, e-mail addresses, phone numbers, links, IP addresses, file paths, your computer's user and host name and, by default, the player's and characters' names (they become *Player* and *Character A, B…*; untick to keep them). You can leave out the story text (prompt, memory and replies) for a private story, choose how many recent turns to include, and leave out the logs. **Before you download, you see what is in the file**: the problems found, what was removed (by kind) and a summary; the file is checked a last time for your keys, user name and home folder, and a file that cannot be made safe is replaced by a note. Pictures, your keys and the rest of your data folder are never included, and **the app never uploads anything**: you attach the file yourself.

| Problem | Try |
|---|---|
| The icon does nothing | Run `./launch.sh` (Windows: `launch.bat`) in a terminal and read the message; check `data/server.log`. The first-time setup (`.venv`) must be done. |
| "needs a key" / no models listed | Paste the API key in Settings → AI & story, press **Save key** next to it, then *Re-scan*. Check the key belongs to that company. |
| A reply is cut off | Raise **Settings → Advanced → Longest reply**, or press **…** (continue) on the reply. With Claude, set *Effort* to Low so hidden thinking doesn't use the budget. |
| Pictures don't appear next to dialogue | The narrator must write portrait tags. Make sure the story prompt's *Images* section tells it to (see Technical notes), and that the character has pictures in 🎭 Cast. |
| Picture drawing fails | 🖼 Art → Picture makers → Test. For ComfyUI, check it is running (Settings → Local models shows this). |
| A local model is very slow | It doesn't fit in video memory. Choose a smaller model, lower the context size, or turn on *Make room for pictures* / use online pictures. |
| The narrator keeps repeating a wrong fact | 🧠 Memory: fix the summary and add a **pinned fact**. |
| The story contradicts itself (eye colour, who is alive, where someone is) | 🧠 Memory → **🔎 Continuity check**. |
| I changed something and want it back | 🧠 Memory → **Backups** (per story) or Settings → Backup → **Restore** (everything). |
| Spending limit says unknown prices | 💲 Usage → Prices: add a row for the model. |

---

## Technical notes

*(For people who want to look under the hood. You do not need these to play.)*

**Browser safety.** The app only answers pages opened as `localhost` or `127.0.0.1`, and a request that changes something must come from the app's own page, so other websites open in your browser cannot use it (for example to press Quit or read your stories). Tools such as `curl` are unaffected.

**Where things are.** `app/` is the server (Python, FastAPI); `static/` is the browser app; `examples/` holds a sample story prompt and a template; `workflows/` holds ComfyUI workflows; `data/` holds everything of yours (stories, pictures, avatars, settings, usage log, backups). Back up `data/` or use Settings → Backup. API keys you enter are stored in `data/config.json` in plain text: keep that folder private and never share or commit it.

**How a turn is built.** Each request is laid out so its start never changes: story prompt, your avatar, the author's cast notes and endings, the picture rules and engine rules; then your notes, the prologue and the memory summary; then the history (append-only); and last, a note attached to your newest message with the live side-panel data, the list of pictures available now (moods and places), and the character memory of whoever is in the scene. This is what makes caching work: Claude marks cache breakpoints after the stable blocks and after the last reply (cached reads cost about 10% of normal; writes cost 25% more for the 5-minute lifetime, 100% more for 1 hour), Ollama reuses its own copy when the prompt start is identical, and other online services cache matching prefixes automatically. Changing the story prompt, cast notes, art options or content setting, or updating the memory summary, makes the next Claude turn rewrite the cache once (🧠 Usage → *Narrator cache* shows how often and why). New pictures do not: the list of moods and places rides on the newest message. **Keep warm** sends a 1-token request shortly before the cache would expire. The line under each reply shows cached versus new tokens.

**Portrait tags.** The narrator writes `[[Name/mood]]` (or the longer `![](portrait:Name/mood)`, both work) on its own line above each line of dialogue; the app shows the matching picture. The app adds a PORTRAITS section to the instructions listing the available names and moods (characters with identical moods share one line). A story prompt that works well says, in its *Images* section: put the tag directly above every NPC dialogue line, spelled exactly as listed, mood lowercase with its number, and show it in the dialogue-format example.

**Status panels.** If the story prompt makes the narrator print a status panel (a fenced `Info` block with a `Characters──` section of rows like `Name:👤/❤️95(+1)/Location/"thought"`), the side panel can read it. Otherwise the state block or the AI fills the panel.

**ComfyUI on Windows.** For *Start and stop ComfyUI automatically*, set *How to start ComfyUI* to a `.bat` file that starts it (the portable ComfyUI download includes `run_nvidia_gpu.bat`; a path with spaces needs quotes). The default on Windows is `ComfyUI\start.bat` in your user folder.

**ComfyUI (local pictures).** Install ComfyUI from github.com/comfyanonymous/ComfyUI, put an SDXL-class checkpoint in `models/checkpoints`, start it (it should listen on `127.0.0.1:8188`), and select it under 🖼 Art → Picture makers. The built-in starter workflow is plain text-to-image. For consistent faces the project includes `workflows/comfyui-ipadapter-face.json`, a workflow with an IPAdapter face node that takes up to three reference pictures; it needs the IPAdapter custom node and its models installed in ComfyUI. Workflows use placeholders: `{{prompt}} {{negative}} {{seed}} {{width}} {{height}} {{model}}` and `{{ref1}}…{{ref3}}` for reference pictures. Anime SDXL models follow short tag-style prompts best, so each picture service has a *Prompt style* (Tags or Sentences). Scene descriptions are kept safe for work.

**Automatic image rules.** Every turn the app adds a short *Images in this app* note, so prompts written for other sites (image keywords, outside picture links) can't make the narrator print pictures that can't be shown. You don't edit your prompt for this. **New-character art (opt-in).** In 🖼 Art → Style & options, tick *Let the narrator flag new named characters*: the narrator then ends a reply with a hidden `<newchar>Name | looks</newchar>` tag when someone new appears, the character joins the cast with that description, and the 🎨 banner offers to draw them. Once they have pictures, portrait tags are used automatically. It's off by default.

**Picture services.** 🖼 Art → *Picture makers* (also 🖼 Picture makers on the Home screen) shows one collapsed card each for Gemini, Grok (xAI), OpenAI and Local (ComfyUI), with a short status (key saved, running…). Open one only when you need its settings. Its model box lists what that service really offers and is the same choice as the Pictures line on *AI & story*: change either and the other follows. On *AI & story*, an **Art style** dropdown under Pictures lists styles that suit your detected local checkpoint(s) and any online service with a key; it sets the open story's art style and is the starting style for new stories.

**Picture types and art style.** 🖼 Art → Picture makers has a *Basic set* and an *Extended set* of picture types (moods) drawn for each new character. Add your own to either list, one per line: just a name (`blushing`) or a name with what to draw (`blushing: embarrassed, red cheeks`). The narrator is told to use a character's existing pictures before asking for a new one; if it asks for one and you draw it, that type is added to the end of the Extended set automatically. **Art style:** every picture follows the story's *art direction*. Pick a ready-made style (anime, light-novel, manga, semi-realistic, realistic, comic, watercolor, painterly, chibi) or write your own: in the creator's trackers/art section, 🖼 Art → Style & options, or at the top of the *Art for new characters* window, which asks for one when a story has none. When a character already has pictures, new pictures are told to match the style of those pictures first.

**Action moods.** Two more sets of picture types sit next to Basic and Extended (each editable the same way): an **Action set** (combat, strike, guard, dodge, charge, leap, aim, cast, draw weapon, wounded, knocked down, defeated, exhausted, flee, sneak, battle cry, victory, dead) and, **only in stories with Adult content on**, an **Adult action set** of its own (aroused, seductive, undressing, lingerie, showering, bathing, nude, climaxing, afterglow). Draw either from a character's page (*Draw the set*) or right after choosing a face; the adult one is hidden, refused and left out of the narrator's instructions unless the story's adult switch is on, and its pictures are drawn by the adult picture maker when one is set up. Action pictures are drawn as a three-quarter shot with the face and upper body as the focus, shown cropped to the upper body in the story, and open whole when clicked, like combat and dead. With **Art → Style & options → Action moods** (on by default; untick to turn it off) the narrator is told to prefer these over calm expressions in fights, chases and danger (and, in adult stories, intimate scenes), and gets a reminder each turn while the last exchange was action.

**Portrait packs.** 🎭 Cast → *Import portraits…* reads a folder with one sub-folder per character and pictures named for moods (`Mara/Angry 1.webp`). It shows a preview first, copies (never moves) files, and skips pictures already imported. Folders named like `Sub-NPCS` or `Extras` become one-picture characters; folders like `Intro` or `Gallery` are skipped.

**Optional, halve the memory a local model's context uses** (run once):

```bash
sudo systemctl edit ollama   # add under [Service]: Environment="OLLAMA_FLASH_ATTENTION=1" and Environment="OLLAMA_KV_CACHE_TYPE=q8_0"
sudo systemctl restart ollama
```

**Adding another AI service.** Anything with an OpenAI-compatible API (OpenRouter, LM Studio, DeepSeek…) can be added under Settings → Advanced → Providers. Provider code lives in `app/providers.py`.

## License and privacy

Released under the MIT License (see `LICENSE`). The app has no accounts and sends no usage data anywhere. Text is sent only to the AI service you choose (and picture prompts to the picture service you choose); with local models nothing leaves your computer.
