"""Shared Elo engine. Each league keeps its own rating table (per team or per
player for tennis). Ratings persist in data/elo_{key}.json between runs."""
import math

BASE_RATING = 1500.0


def expected(ra, rb):
    return 1.0 / (1.0 + 10 ** ((rb - ra) / 400.0))


def mov_multiplier(margin, elo_diff):
    """Margin-of-victory damping (538-style) so blowouts count more, but
    autocorrelation is controlled."""
    return math.log(abs(margin) + 1) * (2.2 / (abs(elo_diff) * 0.001 + 2.2))


def update(ratings, ida, idb, score_a, score_b, k, use_mov=True, draw=False):
    ra = ratings.get(ida, BASE_RATING)
    rb = ratings.get(idb, BASE_RATING)
    ea = expected(ra, rb)
    if draw:
        sa = 0.5
    else:
        sa = 1.0 if score_a > score_b else 0.0
    mult = 1.0
    if use_mov and score_a != score_b:
        mult = mov_multiplier(score_a - score_b, ra - rb)
    delta = k * mult * (sa - ea)
    ratings[ida] = ra + delta
    ratings[idb] = rb - delta


def win_prob(ratings, ida, idb, home_adv_a=0.0, adj_a=0.0):
    """Probability side A wins. home_adv_a: Elo points added if A is home.
    adj_a: extra Elo adjustment for A (injuries etc, can be negative)."""
    ra = ratings.get(ida, BASE_RATING) + home_adv_a + adj_a
    rb = ratings.get(idb, BASE_RATING)
    return expected(ra, rb)
