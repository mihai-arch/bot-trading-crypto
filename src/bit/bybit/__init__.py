"""
bit.bybit — Bybit v5 API layer.

BybitRestClient : thin async HTTP wrapper (no domain knowledge).
parsers         : pure functions mapping raw API dicts → domain models.

Nothing in this package should import from bit.services or bit.strategies.
"""
