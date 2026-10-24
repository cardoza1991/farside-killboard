"""Discord gateway bot (outbound only). Requires discord.py 2.x."""
import asyncio, datetime as dt, logging, os, time
import discord
from discord import app_commands
from discord.ext import tasks
from . import db, feed, logic, service, stats as S

log = logging.getLogger("farside")
PERIOD = [app_commands.Choice(name=p, value=p) for p in ("24h", "7d", "30d", "all")]


class SearchView(discord.ui.View):
    def __init__(self, bot, text, total):
        super().__init__(timeout=300)
        self.bot, self.text, self.page = bot, text, 0
        self.pages = max(1, -(-total // logic.PER_PAGE))
        self._sync()

    def _sync(self):
        self.prev.disabled = self.page <= 0
        self.next.disabled = self.page >= self.pages - 1

    async def _show(self, inter):
        e, _ = await self.bot.q(logic.search, self.text, self.page)
        self._sync()
        await inter.response.edit_message(embed=discord.Embed.from_dict(e), view=self)

    @discord.ui.button(label="◀ Prev", style=discord.ButtonStyle.secondary)
    async def prev(self, inter, _b):
        self.page -= 1
        await self._show(inter)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next(self, inter, _b):
        self.page += 1
        await self._show(inter)


class KillBot(discord.Client):
    def __init__(self, dbpath):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)
        self.dbpath = dbpath
        self.c = db.connect(dbpath)
        self.c.close()
        self.filters = service.filters_from_env()
        self.channel_id = int(os.environ.get("KILLFEED_CHANNEL_ID") or 0)
        self.webhook = os.environ.get("DISCORD_WEBHOOK_URL", "")
        self.poll_s = max(30, int(os.environ.get("POLL_SECONDS", "45")))
        self.group = os.environ.get("HOME_CORP", (os.environ.get("FILTER_NAMES", "DXD").split(",")[0] or "DXD").strip())
        _register(self)

    async def q(self, fn, *a, **k):
        def run():
            c = db.connect(self.dbpath)
            try:
                return fn(c, *a, **k)
            finally:
                c.close()
        return await asyncio.to_thread(run)

    async def setup_hook(self):
        gid = os.environ.get("GUILD_ID")
        if gid:
            g = discord.Object(id=int(gid))
            self.tree.copy_global_to(guild=g)
            try:
                await self.tree.sync(guild=g)
            except discord.Forbidden:
                log.warning("no access to GUILD_ID %s for command sync; falling back to global sync", gid)
                self.tree.clear_commands(guild=g)
                await self.tree.sync()
        else:
            await self.tree.sync()
        self.ingest_loop.change_interval(seconds=self.poll_s)
        self.ingest_loop.start()
        t = os.environ.get("SUMMARY_TIME_UTC", "")
        if t:
            h, m = (int(x) for x in t.split(":"))
            self.summary_loop.change_interval(time=dt.time(h, m, tzinfo=dt.timezone.utc))
            self.summary_loop.start()

    async def on_ready(self):
        log.info("logged in as %s", self.user)
        log.info("in guilds: %s", ", ".join(f"{g.name} ({g.id})" for g in self.guilds) or "NONE")

    async def send(self, embed):
        ch = self.get_channel(self.channel_id) if self.channel_id else None
        if ch is None and self.channel_id:
            try:
                ch = await self.fetch_channel(self.channel_id)
            except discord.HTTPException:
                ch = None
        if ch is not None:
            await ch.send(embed=discord.Embed.from_dict(embed))
            return True
        if self.webhook:
            return await asyncio.to_thread(feed.post_webhook, self.webhook, embed)
        return False

    @tasks.loop(seconds=45)
    async def ingest_loop(self):
        try:
            await self.q(lambda c: db.backfill(c, feed.fetch_page, log=log.info))
            items = await self.q(service.poll_once, self.filters)
        except Exception as e:  # network/API errors: keep looping
            log.warning("poll failed: %s", type(e).__name__)
            return
        for km, kind in items:
            if await self.send(feed.build_embed(km, kind)):
                await self.q(service.mark_posted, km["id"])
            await asyncio.sleep(1)

    @tasks.loop(hours=24)
    async def summary_loop(self):
        try:
            e, _ = await self.q(logic.corp, self.group, "24h")
            e["title"] = "Daily summary · " + e["title"]
            await self.send(e)
            if dt.datetime.now(dt.timezone.utc).strftime("%a").lower() == os.environ.get("WEEKLY_SUMMARY_DAY", "").lower()[:3]:
                e, _ = await self.q(logic.corp, self.group, "7d")
                e["title"] = "Weekly summary · " + e["title"]
                await self.send(e)
        except logic.NotFound:
            pass


def _register(bot):
    tree = bot.tree

    async def reply(inter, fn, *a, view=None):
        await inter.response.defer(thinking=True)
        try:
            e, extra = await bot.q(fn, *a)
        except logic.NotFound as ex:
            await inter.followup.send(str(ex), ephemeral=True)
            return None
        kw = {"embed": discord.Embed.from_dict(e)}
        if view:
            kw["view"] = view(extra)
        await inter.followup.send(**kw)
        return extra

    async def corp_ac(inter, cur):
        return [app_commands.Choice(name=n, value=v) for n, v in await bot.q(S.autocomplete, "corp", cur)]

    async def pilot_ac(inter, cur):
        return [app_commands.Choice(name=n, value=v) for n, v in await bot.q(S.autocomplete, "pilot", cur)]

    @tree.command(description="Search kills by pilot, corp, ship or system")
    async def search(inter: discord.Interaction, text: str):
        await reply(inter, logic.search, text, 0, view=lambda total: SearchView(bot, text, total))

    @tree.command(description="Show one kill by ID (prefix ok)")
    async def kill(inter: discord.Interaction, id: str):
        await reply(inter, logic.kill, id)

    @tree.command(description="Corp stats")
    @app_commands.autocomplete(name=corp_ac)
    @app_commands.choices(period=PERIOD)
    async def corp(inter: discord.Interaction, name: str, period: str = "7d"):
        await reply(inter, logic.corp, name, period)

    @tree.command(description="Pilot stats")
    @app_commands.autocomplete(name=pilot_ac)
    @app_commands.choices(period=PERIOD)
    async def pilot(inter: discord.Interaction, name: str, period: str = "all"):
        await reply(inter, logic.pilot, name, period)

    @tree.command(description="Top corps or pilots")
    @app_commands.choices(kind=[app_commands.Choice(name=x, value=x) for x in ("corps", "pilots")], period=PERIOD,
                          sort=[app_commands.Choice(name=x, value=x) for x in ("kills", "value", "efficiency")])
    async def leaderboard(inter: discord.Interaction, kind: str = "corps", period: str = "7d", sort: str = "kills"):
        await reply(inter, logic.leaderboard, kind, period, sort)

    @tree.command(description="Compare two corps, with head-to-head")
    @app_commands.autocomplete(corp_a=corp_ac, corp_b=corp_ac)
    @app_commands.choices(period=PERIOD)
    async def compare(inter: discord.Interaction, corp_a: str, corp_b: str, period: str = "all"):
        await reply(inter, logic.compare, corp_a, corp_b, period)

    @tree.command(description="DXD summary")
    @app_commands.choices(period=PERIOD)
    async def dxd(inter: discord.Interaction, period: str = "7d"):
        await reply(inter, logic.corp, bot.group, period)

    @tree.command(description="Server-wide totals")
    @app_commands.choices(period=PERIOD)
    async def stats(inter: discord.Interaction, period: str = "all"):
        await reply(inter, logic.server, period)


def run(dbpath):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        raise SystemExit("DISCORD_TOKEN not set in .env")
    KillBot(dbpath).run(token, log_handler=None)
