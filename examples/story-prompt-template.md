# Story prompt template

A story prompt is the instruction sheet the AI narrator reads every turn. A good one is short enough to be read every time and specific enough to keep the story consistent.
Use this outline, and see `the-lighthouse-keepers.md` for a filled-in example.

```
## World
Where and when. The situation at the start. The tone (grim, cosy, funny, romantic…).

## Characters
- **Name**: role, looks, personality, what they want, how they speak.
(Only the characters the narrator must keep consistent. You can also premake the cast with pictures in the story creator.)

## Writing style
Person and tense, how long replies should be, how narration and dialogue are formatted,
and the rule "never write {user}'s actions, words or feelings". {user} is replaced with your character's name.

## Rules
Game mechanics, stats, how fast relationships change, how consequences work, anything the narrator must never do.

## Images   (only if you use character portraits)
Tell the narrator to put `![](portrait:Name/mood)` on its own line above each NPC dialogue line,
spelled exactly as listed in the PORTRAITS section the app adds.

## Dialogue format   (only if you use portraits or a specific layout)
An example of one exchange, including the portrait tag lines.

## Quests   (optional)
Tell the narrator to invent quests as the story goes, with objectives and a reward scaled to difficulty, to announce them in the story,
and to grant the reward in the same reply the quest is completed. Pair it with a *Quest log* tracker (creator → Trackers) so quests show in the side panel.

## Status panel   (optional)
A fenced ```Info block the narrator prints at the top of every reply. The app can read rows like
Name:👤/❤️95(+1)/Location/"thought" to fill the side panel. Or skip this and let the app track things itself
(Settings → AI & story → Side panel).
```

Tips
- Put what must never change (rules, formats) in the prompt; put what changes (events, relationships) in play. The app keeps the memory.
- You do not need to explain pictures in the prompt: the app adds its own image rules and ignores image keywords or links copied from other sites.
- Test it with **🧪 Test this prompt** in the story creator before you play.
- Longer prompts cost more on online AIs, but the app's prompt caching makes repeated reading cheap.
