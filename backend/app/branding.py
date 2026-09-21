"""Product naming.

Different organisations deploy this tracker, so no organisation's name is
baked into templates, page titles or the API. Both the product name and the
name of whoever runs a given deployment are configuration; changing either is
an environment variable, not a search-and-replace across the codebase.

Two names are kept separate on purpose:

``product_name``
    The software. This is what a future commercial deployment is sold as.

``publisher_name``
    Whoever runs a particular deployment. It appears wherever the site speaks
    in an organisation's own voice — an editorial watchlist, for instance,
    belongs to the publisher, not to the software. It defaults to empty, and
    the interface falls back to naming the thing rather than inventing an
    organisation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Brand:
    product_name: str
    product_tagline: str
    publisher_name: str
    publisher_url: str
    #: How the publisher's editorial chemical list is labelled in the UI.
    watchlist_label: str

    @property
    def watchlist_flag_label(self) -> str:
        return f"RED — {self.watchlist_label}"


DEFAULT_PRODUCT_NAME = "Ground Truth"
DEFAULT_TAGLINE = "Public records of what was sprayed, where"
#: Used when no publisher is configured. Says what the flag *is* rather than
#: attributing it to an organisation that has not been named.
DEFAULT_WATCHLIST_LABEL = "Editorial Watchlist"


@lru_cache
def get_brand() -> Brand:
    publisher = os.environ.get("TRACKER_PUBLISHER_NAME", "").strip()
    default_watchlist = (
        f"{publisher} Watchlist" if publisher else DEFAULT_WATCHLIST_LABEL
    )
    return Brand(
        product_name=os.environ.get("TRACKER_PRODUCT_NAME", DEFAULT_PRODUCT_NAME),
        product_tagline=os.environ.get("TRACKER_PRODUCT_TAGLINE", DEFAULT_TAGLINE),
        publisher_name=publisher,
        publisher_url=os.environ.get("TRACKER_PUBLISHER_URL", ""),
        watchlist_label=os.environ.get("TRACKER_WATCHLIST_LABEL", default_watchlist),
    )
