# Farside Discord killboard

Posts every new Farside kill (from https://playfarside.com/kills) to a Discord channel as a rich embed, through a channel **webhook**. No bot token, no gateway, no listening sockets. Python stdlib only.

## Data source
The public JSON endpoint the kills page itself uses (no auth):
`GET https://playfarside.com/api/killmails?limit=30[&cursor=<nextCursor>][&corporationId=|pilotId=|shipId=|systemId=]`
The response is `{killmails:[...], nextCursor}`. Each kill has an id, occurredAt (ms), totalValue (credits), victim{pilot,ship}, attackers[], finalBlow, attackerCount and location{poiName,systemName,regionName,securityBand}. The server caches for 15 s, so the bot polls every 45 s (minimum 30 s) and backs off up to 15 min on errors. robots.txt allows `/`.

## Setup
1. In Discord, open the channel's **Edit Channel > Integrations > Webhooks > New Webhook**, name it, then **Copy Webhook URL**.
2. `cp .env.example .env`, paste the URL into `DISCORD_WEBHOOK_URL=`, then `chmod 600 .env`.
3. Optional: set `FILTER_NAMES=DXD,Space Monkey` (callsigns, corp names, tickers or IDs). With a filter set, only kills involving those names are posted: green for your kills, red for your losses. Without one, every kill is posted in grey.
4. Test it without posting: `python3 killboard.py --dry-run --once`
5. Run it: `python3 killboard.py`, or install the service (see below).

On the first run, existing kills are marked as seen and nothing is posted. Use `--backfill N` (or `BACKFILL=N`) to post the N most recent kills on that first run. Seen IDs are kept in `state.json` (the last 5000).

## Config (.env)
| var | meaning |
|---|---|
| DISCORD_WEBHOOK_URL | required, keep secret |
| FILTER_NAMES | optional comma list |
| CORPORATION_ID | optional: fetch only this corp's board |
| POLL_SECONDS | default 45, min 30 |
| BACKFILL | first-run backfill count |

## Service (systemd --user)
```
cp farside-killboard.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now farside-killboard
journalctl --user -u farside-killboard -f
```

## Tests
`python3 -m unittest discover -s tests -v` (uses the recorded fixture in tests/fixtures/)
