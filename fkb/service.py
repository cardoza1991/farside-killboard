"""Shared ingest cycle used by the bot and the webhook fallback."""
import fcntl, os, sys
from . import db, feed


def acquire_lock(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fh = open(path, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        sys.exit(f"another killboard instance holds {path}")
    fh.write(str(os.getpid()))
    fh.flush()
    return fh


def poll_once(c, filters, fetch_page=feed.fetch_page, max_pages=5):
    """Fetch newest pages until a known kill appears; ingest; return [(km, kind)] unposted & matching filter."""
    cursor, kms = None, []
    for _ in range(max_pages):
        d = fetch_page(cursor)
        page = d.get("killmails") or []
        kms += page
        if any(c.execute("SELECT 1 FROM kills WHERE id=?", (k.get("id"),)).fetchone() for k in page):
            break
        cursor = d.get("nextCursor")
        if not cursor:
            break
    db.ingest(c, kms, posted=0)
    out = []
    for r in c.execute("SELECT id FROM kills WHERE posted=0 ORDER BY occurred_at").fetchall():
        km = db.get_raw(c, r[0])
        kind = feed.classify(km, filters)
        if kind and (filters or kind == "neutral"):
            out.append((km, kind))
        else:
            mark_posted(c, r[0])
    return out


def mark_posted(c, kid):
    c.execute("UPDATE kills SET posted=1 WHERE id=?", (kid,))
    c.commit()


def filters_from_env():
    return {x.strip().lower() for x in os.environ.get("FILTER_NAMES", "").split(",") if x.strip()}
