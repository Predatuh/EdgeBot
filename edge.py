"""Value math against Kalshi prices (which are already probabilities)."""


def devig(probs):
    """Normalize a list of side probabilities so they sum to 1."""
    vals = [p for p in probs if p is not None]
    s = sum(vals)
    if not vals or s <= 0:
        return probs
    return [(p / s if p is not None else None) for p in probs]


def kelly_units(p, price, fraction=0.25, cap=1.0):
    """Quarter-Kelly stake in units. Buying YES at `price` pays (1-price)/price per unit."""
    if not price or price <= 0 or price >= 1:
        return 0.0
    b = (1 - price) / price
    k = (b * p - (1 - p)) / b
    return round(max(0.0, min(cap, k * fraction * 10)), 2)


def profit_per_unit(price):
    return (1 - price) / price if price and 0 < price < 1 else 0.0
