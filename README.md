# Farside Discord killboard bot

Stores **every** Farside kill in SQLite (`data/kills.db`), auto-posts DXD kills (green) and losses (red) to a channel, and answers slash commands with search and stats. It only makes outbound connections (the Discord gateway and the playfarside.com API) and never listens on a port. There's no LLM.

## Data source
`GET https://playfarside.com/api/killmails?limit=30[&cursor=<nextCursor>]`, the public JSON the /kills page itself uses. The bot polls every 45 s (minimum 30 s), and the server caches responses for 15 s. On the first start it backfills history by following the cursor (one page every 2.5 s, capped at 30 days or 20k kills, resumable), then keeps up incrementally. Kills are deduped by ID. Values are in game **credits**.

## 1. Create the Discord bot
1. Go to https://discord.com/developers/applications, then **New Application** (e.g. "Farside Killboard").
2. Under **Bot**, click **Reset Token** and copy it into `.env` as `DISCORD_TOKEN=`. Never share it. No privileged intents are needed.
3. Under **OAuth2 > URL Generator**, tick the scopes **bot** and **applications.commands**, and the permissions **View Channels**, **Send Messages** and **Embed Links**. Open the generated URL and add the bot to your server. It looks like this:
   `https://discord.com/api/oauth2/authorize?client_id=<APP_ID>&scope=bot%20applications.commands&permissions=19456`
4. In Discord, turn on **User Settings > Advanced > Developer Mode**. Right-click the kill-feed channel, choose **Copy Channel ID** and set it as `KILLFEED_CHANNEL_ID`. Right-click the server icon, choose **Copy Server ID** and set it as `GUILD_ID` (commands appear instantly in that server).

## 2. Configure `.env` (see `.env.example`)
`DISCORD_TOKEN`, `KILLFEED_CHANNEL_ID`, `GUILD_ID`, `FILTER_NAMES=DXD`, `HOME_CORP=DXD`, `SUMMARY_TIME_UTC=18:00`, optional `WEEKLY_SUMMARY_DAY=sun`, and an optional fallback `DISCORD_WEBHOOK_URL`. Run `chmod 600 .env`.

## 3. Run
```
PY=/path/to/venv/bin/python   # has discord.py 2.7.1
$PY killboard.py backfill          # optional; the bot also does it on first start
$PY killboard.py run
```
Service: copy `farside-killboard.service` to `~/.config/systemd/user/`, run `systemctl --user daemon-reload`, then `systemctl --user enable --now farside-killboard`. Only one instance can run at a time (lock file `data/killboard.lock`).

## Slash commands
`/search text` (5 per page, with ◀ ▶ buttons) · `/kill id` (an ID prefix works) · `/corp name [period]` · `/pilot name [period]` · `/leaderboard [corps|pilots] [period] [kills|value|efficiency]` · `/compare corp_a corp_b [period]` (includes head-to-head) · `/dxd [period]` · `/stats [period]`. Periods are 24h, 7d, 30d and all. Corp and pilot names autocomplete.

Definitions: a **kill** counts when any corp or pilot member is on the attacker list (friendly fire is excluded). **Destroyed** is the sum of those kills' total value, and **lost** is the sum of losses. **Efficiency** = destroyed / (destroyed + lost). **K/D** = kills / losses. **Solo** means attackerCount == 1.

## Kill cards & hunting
Each feed post and `/kill` comes with a PNG kill card (Pillow): the final-blow pilot and ship on the left, the victim on the right, party size, value and time in the middle, and a large **LOCATION** block (system, POI, region) with a security badge (🟢 core · 🟠 marches · 🔴 deep), plus damage bars. Ship images are cached in `data/ships/`. If an image fails to load, the card shows a placeholder; if the whole card fails, the post falls back to a text embed.

* `/hunt [1h|6h|24h|7d] [corp] [view: hot systems|targets]`: **hot** lists systems by heat (each kill counts 1, plus up to 1 more the more recent it is), with last kill time, typical victim ships, average value and who's hunting there. **targets** lists systems where non-DXD pilots died, most recent first.
* `/where <pilot|corp>`: recent kill and loss locations, last seen, and favourite systems.
Data is only as fresh as the playfarside.com API, which is polled every 45 s.

## CLI (dry-run / no Discord)
```
python3 killboard.py hunt --period 24h [--corp SYN]
python3 killboard.py targets --period 24h
python3 killboard.py where "Space Monkey"
python3 killboard.py card 8629ac91 -o kill.png
```

```
python3 killboard.py stats DXD --period 7d
python3 killboard.py pilot "Space Monkey"
python3 killboard.py leaderboard corps --period all --sort value
python3 killboard.py compare DXD MMW
python3 killboard.py search Kylys
python3 killboard.py kill 8629ac91
python3 killboard.py server
python3 killboard.py poll --dry-run      # one ingest cycle, prints the feed embeds
python3 killboard.py webhook             # legacy webhook-only poster
```

## Tests
`python3 -m unittest discover -s tests -v` uses the recorded API pages in `tests/fixtures/` and checks the SQL stats against an independent pure-Python calculation.
