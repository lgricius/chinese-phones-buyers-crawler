"""Site registry. To add a shop: create a module with NAME,
`async def fetch(client, raw, log)` and `def parse(records, log) -> list[Offer]`,
then list it here."""

from .. import config
from . import giztop, gshopper, tradingshenzhen, wonda

SITES = {m.NAME: m for m in (giztop, wonda, tradingshenzhen, gshopper)}
ENABLED = [n for n in SITES if n not in config.DISABLED_SITES]  # config.DISABLED_SITES: skipped by crawl, index and web
