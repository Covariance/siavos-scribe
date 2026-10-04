# siavos-scribe

Telegram bot that periodically asks whitelisted players an open question from the vault's `wiki/TODO.md` and saves every text/voice reply for later parsing. The vault is read-only for the bot.

## Setup (local, Windows)
1. Create a bot with @BotFather; copy its token.
2. `copy .env.example .env` and set `BOT_TOKEN`.
3. `copy config.example.toml config.toml`; set `admin_id` (your Telegram numeric ID; ask @userinfobot), `whitelist` (players' IDs) and `todo_path`. The bot refuses to start without an admin and a non-empty whitelist.
4. `uv run siavos-scribe --list-questions` — checks the parser against the vault.
5. `uv run siavos-scribe` — starts the bot (long polling; must stay running).

## Usage (in Telegram)
`/start` (pick interval), `/ask` or the "Задать вопрос сейчас" button, `/proofread` or the "Вычитать страницу" button, `/interval 12h`, `/pause`, `/resume`, `/status`.

`/proofread` sends a random wiki page (character, location, faction, item, lore, thread — never a session) as raw markdown without its frontmatter and asks the player to correct it. Pages longer than one Telegram message (4096 chars) go as a `.md` file. A user gets a page they haven't seen before, and only after all are done the least recently sent one. It is on demand only and does not touch the question timer. Replies are saved like any answer; `/asked` shows these with trigger `proofread` and the page name. Configure with `wiki_dir` / `proofread_folders` in `config.toml` (defaults: folder of `todo_path`; all folders except `sessions`).
Intervals: `30m`, `12h`, `3d`, `1d12h` (also `м`/`ч`/`д`); from `min_interval` up to 365 days.
The cadence choice also offers "Только по запросу" (or type `нет` / `/interval off`): the bot then never writes first, only answers `/ask` and `/proofread`. Choosing any interval, or `/resume`, turns the schedule back on. Until a user picks something, `default_interval` applies.

Text, voice, audio, video, video notes, photos and documents are all saved. A reply to a question or page is attributed to it; anything else goes to the most recent one.

## Admin commands (only `admin_id`, private chat)
- `/admin` — this list.
- `/users` — all users: interval, questions asked, messages saved, last activity (also lists whitelisted people who haven't pressed /start).
- `/asked [user] [N]` — questions asked (all users, or one by ID / @username); last 15 by default, max 100. Shows section, trigger (scheduled/manual) and how many replies each got.
- `/answers [user] [N]` — saved submissions with the question each was attributed to.
- `/file <message id>` — the stored voice/media file for a submission (id is the `#N` in `/answers`).
- `/export [user]` — full JSON dump (users, questions_asked, messages incl. raw Telegram JSON).

The admin gets no questions unless also in `whitelist`. Anyone else is ignored (logged only).

## Data (`data/`, git-ignored)
- `scribe.db` — SQLite: `users`, `questions_asked` (per-user history, so questions don't repeat until the pool is exhausted), `messages` (every saved text/voice message, with raw Telegram JSON).
- `files/<user_id>/` — downloaded voice notes and other media.

## Deploy on a VPS (Linux, systemd)
Only one copy of the bot may poll Telegram at a time: stop the local one before starting the VPS one.

1. Get the vault onto the VPS, e.g. `git clone <vault repo> /home/scribe/siavos`. The bot re-reads `TODO.md` whenever its mtime changes, so updating the vault (`git pull`, a cron job, Syncthing…) is enough; no restart needed.
2. As user `scribe`, install uv and the bot:
   ```sh
   curl -LsSf https://astral.sh/uv/install.sh | sh
   git clone git@github.com:Covariance/siavos-scribe.git ~/siavos-scribe
   cd ~/siavos-scribe
   uv sync --frozen --no-dev
   mkdir -p data
   cp .env.example .env && chmod 600 .env          # set BOT_TOKEN
   cp config.example.toml config.toml              # set admin_id, whitelist, todo_path = "/home/scribe/siavos/wiki/TODO.md"
   .venv/bin/siavos-scribe --list-questions        # sanity check
   ```
3. To keep existing history, copy `data/` from your PC (`scp -r data scribe@<vps>:~/siavos-scribe/`) with the local bot stopped.
4. Install the service (as root):
   ```sh
   cp /home/scribe/siavos-scribe/deploy/siavos-scribe.service /etc/systemd/system/
   systemctl daemon-reload
   systemctl enable --now siavos-scribe
   journalctl -u siavos-scribe -f                  # logs
   ```
5. Update later: `cd ~/siavos-scribe && git pull && uv sync --frozen --no-dev`, then `sudo systemctl restart siavos-scribe`.

Back up `data/` regularly (it holds every answer and voice note), e.g. a nightly `sqlite3 data/scribe.db ".backup data/backup.db"` plus copying `data/files/` off the server.

## Tests
`uv run pytest`
