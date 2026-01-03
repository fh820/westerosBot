REAL_SECONDS_PER_GAME_DAY = 600

BASE_SPEEDS = {
    "land": 15,  # Miles per Game Day
    "road": 25,  # Road bonus
    "sea": 75,  # Sea travel is fast
}


def calculate_travel_duration(terrain_breakdown, army_size):
    """
    Calculates the real-time duration of a journey in seconds.
    """

    # 1. Army Size Modifier
    # Logic: Small armies move fast. Massive hosts move slow.
    speed_mod = 1.0
    if army_size < 100:
        speed_mod = 2  # Scout party (Fast)
    elif army_size > 10000:
        speed_mod = 0.75  # Massive host (Slow baggage train)
    elif army_size > 20000:
        speed_mod = 0.5

    # 2. Calculate Total Game Days needed
    # Formula: Distance / (BaseSpeed * Modifier)
    # --- FIX APPLIED HERE ---
    # Use .get(key, 0) to safely handle journeys that don't use all terrain types.
    days_land = terrain_breakdown.get("land", 0) / (BASE_SPEEDS["land"] * speed_mod)
    days_road = terrain_breakdown.get("road", 0) / (BASE_SPEEDS["road"] * speed_mod)
    days_sea = terrain_breakdown.get("sea", 0) / (BASE_SPEEDS["sea"] * speed_mod)
    # --- END OF FIX ---

    total_game_days = days_land + days_road + days_sea

    # 3. Convert to Real Seconds
    total_real_seconds = total_game_days * REAL_SECONDS_PER_GAME_DAY

    return int(total_real_seconds)


def format_duration(seconds):
    """Helper to make it readable for Discord"""
    days = seconds // 86400
    seconds %= 86400
    hours = seconds // 3600
    seconds %= 3600
    minutes = seconds // 60

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")

    return " ".join(parts) if parts else "< 1m"
