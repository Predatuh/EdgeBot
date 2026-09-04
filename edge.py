"""Value math: turn market odds into de-vigged implied probabilities, compare
with the model, and only recommend when the edge clears the threshold.
'Knowing when not to bet' lives here."""


def american_to_prob(ml):
    if ml is None:
        return None
    try:
        ml = float(ml)
    except (TypeError, ValueError):
        return None
    if ml == 0:
        return None
    if ml > 0:
        return 100.0 / (ml + 100.0)
    return -ml / (-ml + 100.0)


def devig(pa, pb):
    """Remove the bookmaker margin from a two-way market."""
    if pa is None or pb is None:
        return pa, pb
    s = pa + pb
    if s <= 0:
        return pa, pb
    return pa / s, pb / s


def profit_per_unit(ml):
    ml = float(ml)
    return ml / 100.0 if ml > 0 else 100.0 / -ml


def evaluate(model_pa, home_ml, away_ml, edge_threshold):
    """Returns (side, edge, ml) where side is 'home'/'away'/None."""
    ia = american_to_prob(home_ml)
    ib = american_to_prob(away_ml)
    if ia is None or ib is None:
        return None, 0.0, None
    fa, fb = devig(ia, ib)
    edge_home = model_pa - fa
    edge_away = (1 - model_pa) - fb
    if edge_home >= edge_threshold and edge_home >= edge_away:
        return "home", edge_home, home_ml
    if edge_away >= edge_threshold:
        return "away", edge_away, away_ml
    return None, max(edge_home, edge_away), None


def kelly_units(p, ml, fraction=0.25, cap=1.0):
    """Quarter-Kelly stake in units, capped."""
    b = profit_per_unit(ml)
    q = 1 - p
    k = (b * p - q) / b
    return round(max(0.0, min(cap, k * fraction * 10)), 2)  # scaled to ~unit sizes


def evaluate_soccer(e_home, home_ml, away_ml, draw_ml, edge_threshold):
    """3-way market. Elo expected score counts a draw as 0.5, so
    P(home win) = e - P(draw)/2, using the market's de-vigged draw prob."""
    ih = american_to_prob(home_ml)
    ia = american_to_prob(away_ml)
    idr = american_to_prob(draw_ml)
    if None in (ih, ia, idr):
        return None, 0.0, None, e_home
    s = ih + ia + idr
    fh, fa, fd = ih / s, ia / s, idr / s
    pw = max(0.0, min(1.0, e_home - fd / 2))
    pa = max(0.0, min(1.0, (1 - e_home) - fd / 2))
    eh, ea = pw - fh, pa - fa
    if eh >= edge_threshold and eh >= ea:
        return "home", eh, home_ml, pw
    if ea >= edge_threshold:
        return "away", ea, away_ml, pa
    return None, max(eh, ea), None, pw
