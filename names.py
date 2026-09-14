"""Canonical team names so Elo is not split across two keys for one club.

Kalshi lists NFL sides by city ("Green Bay"). Settled history and a few ESPN
lookups also stored nickname keys ("GB Packers"). Each key then trained on
half the games and sat near 1500. Folding them together is what lets an ESPN
backfill actually separate the league.

Soccer has the same class of collision (PSG vs Paris Saint-Germain). Unknown
names pass through unchanged.
"""

# Kalshi's live NFL vocabulary is the city/region. Everything else maps here.
NFL_CANON = {
    "GB Packers": "Green Bay", "Green Bay Packers": "Green Bay",
    "LV Raiders": "Las Vegas", "Las Vegas Raiders": "Las Vegas",
    "HOU Texans": "Houston", "Houston Texans": "Houston",
    "SF 49ers": "San Francisco", "San Francisco 49ers": "San Francisco",
    "LA Chargers": "Los Angeles C", "Los Angeles Chargers": "Los Angeles C",
    "Los Angeles Charger": "Los Angeles C",
    "NY Jets": "New York J", "New York Jets": "New York J",
    "PIT Steelers": "Pittsburgh", "Pittsburgh Steelers": "Pittsburgh",
    "CAR Panthers": "Carolina", "Carolina Panthers": "Carolina",
    "JAC Jaguars": "Jacksonville", "Jacksonville Jaguars": "Jacksonville",
    "JAX Jaguars": "Jacksonville",
    "DEN Broncos": "Denver", "Denver Broncos": "Denver",
    "WAS Commanders": "Washington", "Washington Commanders": "Washington",
    "DET Lions": "Detroit", "Detroit Lions": "Detroit",
    "BAL Ravens": "Baltimore", "Baltimore Ravens": "Baltimore",
    "MIN Vikings": "Minnesota", "Minnesota Vikings": "Minnesota",
    "ATL Falcons": "Atlanta", "Atlanta Falcons": "Atlanta",
    "IND Colts": "Indianapolis", "Indianapolis Colts": "Indianapolis",
    "BUF Bills": "Buffalo", "Buffalo Bills": "Buffalo",
    "CLE Browns": "Cleveland", "Cleveland Browns": "Cleveland",
    "NO Saints": "New Orleans", "New Orleans Saints": "New Orleans",
    "LA Rams": "Los Angeles R", "Los Angeles Rams": "Los Angeles R",
    "NY Giants": "New York G", "New York Giants": "New York G",
    "MIA Dolphins": "Miami", "Miami Dolphins": "Miami",
    "CHI Bears": "Chicago", "Chicago Bears": "Chicago",
    "CIN Bengals": "Cincinnati", "Cincinnati Bengals": "Cincinnati",
    "PHI Eagles": "Philadelphia", "Philadelphia Eagles": "Philadelphia",
    "NE Patriots": "New England", "New England Patriots": "New England",
    "KC Chiefs": "Kansas City", "Kansas City Chiefs": "Kansas City",
    "TB Buccaneers": "Tampa Bay", "Tampa Bay Buccaneers": "Tampa Bay",
    "DAL Cowboys": "Dallas", "Dallas Cowboys": "Dallas",
    "ARI Cardinals": "Arizona", "Arizona Cardinals": "Arizona",
    "SEA Seahawks": "Seattle", "Seattle Seahawks": "Seattle",
    "TEN Titans": "Tennessee", "Tennessee Titans": "Tennessee",
}

# Only the ones prefix-matching will not catch. ESPN displayName is the value
# when we need a lookup the other way; here the key is whatever Kalshi sent.
SOCCER_CANON = {
    "PSG": "Paris Saint-Germain",
    "Paris SG": "Paris Saint-Germain",
    "Paris Saint Germain": "Paris Saint-Germain",
    "Man City": "Manchester City",
    "Man United": "Manchester United",
    "Man Utd": "Manchester United",
    "Spurs": "Tottenham Hotspur",
    "Tottenham": "Tottenham Hotspur",
    "Inter": "Internazionale",
    "Inter Milan": "Internazionale",
    "Athletic Bilbao": "Athletic Club",
    "Atletico": "Atletico Madrid",
    "Atlético": "Atletico Madrid",
    "Atletico de Madrid": "Atletico Madrid",
    "Bayern": "Bayern Munich",
    "Bayern München": "Bayern Munich",
    "Dortmund": "Borussia Dortmund",
    "Leverkusen": "Bayer Leverkusen",
    "M'gladbach": "Borussia Monchengladbach",
    "Gladbach": "Borussia Monchengladbach",
    "Wolves": "Wolverhampton",
    "Newcastle": "Newcastle United",
    "West Ham": "West Ham United",
    "Nottingham Forest": "Nottingham Forest",
    "Brighton": "Brighton & Hove Albion",
    "Brighton and Hove Albion": "Brighton & Hove Albion",
    "Sheffield Utd": "Sheffield United",
    "Leicester": "Leicester City",
    "Leeds": "Leeds United",
    "NYCFC": "New York City FC",
    "NY Red Bulls": "New York Red Bulls",
    "LAFC": "Los Angeles FC",
    "LA Galaxy": "LA Galaxy",
    "Inter Miami": "Inter Miami CF",
    "Atlanta United": "Atlanta United FC",
}

SOCCER_LEAGUES = {"epl", "laliga", "ligue1", "seriea", "bundesliga", "mls", "ucl"}


def _table(league):
    if league == "nfl":
        return NFL_CANON
    if league in SOCCER_LEAGUES:
        return SOCCER_CANON
    return {}


def canon(league, name):
    """The name this league's Elo table should use, or the input unchanged."""
    if not name or name in ("Tie", "VOID"):
        return name
    table = _table(league)
    if name in table:
        return table[name]
    low = {k.lower(): v for k, v in table.items()}
    return low.get(name.lower().strip(), name)


def fold_elo(league, ratings):
    """Merge alias keys into the canonical one. Canonical wins when both exist
    because that is the name current Kalshi events use; an alias-only team
    moves over so its games are not thrown away."""
    if not ratings:
        return ratings
    table = _table(league)
    if not table:
        return ratings
    out = dict(ratings)
    for alias, dest in table.items():
        if alias not in out or alias == dest:
            continue
        val = out.pop(alias)
        if dest not in out:
            out[dest] = val
        # both present: keep dest (the live Kalshi name). The backfill will
        # overwrite both from ESPN scores anyway.
    return out


def fold_history(league, hist):
    """Rewrite a/b/w onto canonical names so form and H2H see one team."""
    if not hist or not _table(league):
        return hist
    out = []
    seen = set()
    for g in hist:
        row = dict(g)
        row["a"] = canon(league, g.get("a", ""))
        row["b"] = canon(league, g.get("b", ""))
        w = g.get("w", "")
        row["w"] = w if w in ("Tie", "VOID", "") else canon(league, w)
        sig = (row.get("d"), row["a"], row["b"])
        if sig in seen:
            continue
        seen.add(sig)
        out.append(row)
    return out
