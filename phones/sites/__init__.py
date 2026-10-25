"""Site registry. To add a shop: create a module with NAME,
`async def fetch(client, raw, log)` and `def parse(records, log) -> list[Offer]`,
then list it here."""

from .. import config
from . import geekwills, giztop, gshopper, tradingshenzhen, trinity, wonda

SITES = {m.NAME: m for m in (giztop, wonda, tradingshenzhen, gshopper, geekwills, trinity)}
ENABLED = [n for n in SITES if n not in config.DISABLED_SITES]  # config.DISABLED_SITES: skipped by crawl, index and web
