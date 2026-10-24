#!/usr/bin/env python3
"""Farside killboard: Discord bot + CLI.

  killboard.py run                      # Discord bot (needs DISCORD_TOKEN), outbound gateway only
  killboard.py webhook [--dry-run]      # legacy webhook poster (no bot)
  killboard.py backfill                 # one-time history fetch into data/kills.db
  killboard.py poll [--dry-run]         # one ingest cycle, print/post feed embeds
  killboard.py stats DXD --period 7d    # corp stats (text)
  killboard.py pilot NAME | hunt | targets | where NAME | card ID -o x.png | leaderboard [corps|pilots] | compare A B | search TEXT | kill ID | server
"""
import argparse, json, os, random, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fkb import db, feed, logic, render as R, service, stats as S  # noqa: E402


def main(argv=None):
    feed.load_env(os.path.join(HERE, ".env"))
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("DB_PATH", os.path.join(HERE, "data", "kills.db")))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    for n in ("webhook", "poll"):
        p = sub.add_parser(n)
        p.add_argument("--dry-run", action="store_true")
    b = sub.add_parser("backfill")
    b.add_argument("--days", type=int, default=30)
    b.add_argument("--max", type=int, default=20000)
    for n, arg in (("stats", "corp"), ("pilot", "name")):
        p = sub.add_parser(n)
        p.add_argument(arg)
        p.add_argument("--period", default="7d" if n == "stats" else "all", choices=list(S.PERIODS))
    p = sub.add_parser("leaderboard")
    p.add_argument("kind", nargs="?", default="corps", choices=["corps", "pilots"])
    p.add_argument("--period", default="7d", choices=list(S.PERIODS))
    p.add_argument("--sort", default="kills", choices=["kills", "value", "efficiency"])
    p.add_argument("--limit", type=int, default=10)
    p = sub.add_parser("compare")
    p.add_argument("a")
    p.add_argument("b")
    p.add_argument("--period", default="all", choices=list(S.PERIODS))
    p = sub.add_parser("search")
    p.add_argument("text")
    p.add_argument("--page", type=int, default=0)
    sub.add_parser("kill").add_argument("id")
    p = sub.add_parser("hunt")
    p.add_argument("--period", default="24h", choices=list(S.PERIODS))
    p.add_argument("--corp")
    p = sub.add_parser("targets")
    p.add_argument("--period", default="24h", choices=list(S.PERIODS))
    sub.add_parser("where").add_argument("name")
    p = sub.add_parser("card")
    p.add_argument("id")
    p.add_argument("-o", "--out", default="kill.png")
    p = sub.add_parser("server")
    p.add_argument("--period", default="all", choices=list(S.PERIODS))
    a = ap.parse_args(argv)

    if a.cmd == "run":
        lock = service.acquire_lock(os.path.join(os.path.dirname(a.db), "killboard.lock"))  # noqa: F841
        from fkb import bot
        return bot.run(a.db)

    c = db.connect(a.db)
    try:
        if a.cmd == "backfill":
            meta = db.meta_get(c, "backfill_done")
            n = db.backfill(c, feed.fetch_page, a.days, a.max)
            print("backfill already done" if meta == "1" else f"backfill fetched {n} kills")
            print(f"db now holds {c.execute('SELECT COUNT(*) FROM kills').fetchone()[0]} kills")
        elif a.cmd in ("poll", "webhook"):
            lock = service.acquire_lock(os.path.join(os.path.dirname(a.db), "killboard.lock"))  # noqa: F841
            webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
            if not a.dry_run and not webhook:
                sys.exit("DISCORD_WEBHOOK_URL not set (or use --dry-run)")
            filters = service.filters_from_env()
            interval = max(30, int(os.environ.get("POLL_SECONDS", "45")))
            backoff = interval
            while True:
                try:
                    db.backfill(c, feed.fetch_page)
                    for km, kind in service.poll_once(c, filters):
                        e = feed.build_embed(km, kind)
                        if a.dry_run:
                            print(json.dumps(e, indent=2))
                        elif not feed.post_webhook(webhook, e):
                            continue
                        service.mark_posted(c, km["id"])
                    backoff = interval
                except Exception as ex:
                    backoff = min(backoff * 2, 900)
                    print(f"poll failed ({type(ex).__name__}); retry in {backoff}s", file=sys.stderr)
                if a.cmd == "poll":
                    break
                time.sleep(backoff + random.uniform(0, 5))
        elif a.cmd == "stats":
            r = S.find_corp(c, a.corp) or sys.exit(f"no corp '{a.corp}'")
            print(R.entity_lines(R.corp_label(r), S.entity_stats(c, "corp", r["id"], a.period), a.period))
        elif a.cmd == "pilot":
            r = S.find_pilot(c, a.name) or sys.exit(f"no pilot '{a.name}'")
            print(R.entity_lines(r["callsign"], S.entity_stats(c, "pilot", r["id"], a.period), a.period))
        elif a.cmd == "leaderboard":
            print(R.leaderboard_lines(S.leaderboard(c, a.kind, a.period, a.sort, a.limit), a.kind, a.period, a.sort))
        elif a.cmd == "card":
            try:
                png, _, km = logic.card_for(c, a.id, service.filters_from_env(), os.path.join(os.path.dirname(a.db), "ships"))
            except logic.NotFound as ex:
                sys.exit(str(ex))
            open(a.out, "wb").write(png)
            print(f"wrote {a.out} for kill {km['id']}")
        else:
            fn = {"hunt": lambda: logic.hunt(c, a.period, a.corp), "targets": lambda: logic.targets(c, a.period, os.environ.get("HOME_CORP", "DXD")),
                  "where": lambda: logic.where(c, a.name),"compare": lambda: logic.compare(c, a.a, a.b, a.period), "search": lambda: logic.search(c, a.text, a.page),
                  "kill": lambda: logic.kill(c, a.id), "server": lambda: logic.server(c, a.period)}[a.cmd]
            try:
                print(json.dumps(fn()[0], indent=2))
            except logic.NotFound as ex:
                sys.exit(str(ex))
    finally:
        c.close()


if __name__ == "__main__":
    main()
