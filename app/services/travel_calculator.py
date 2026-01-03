# Time Settings
# 1 Game Day = 40 Minutes IRL (2400 seconds)
# This makes Winterfell -> KL take exactly 24 hours for a normal army.
REAL_SECONDS_PER_GAME_DAY = 2400

# Map Scale (From your calibration script)
PIXELS_PER_MILE = 1.0451

# Speeds (Miles per Game Day)
BASE_SPEEDS = {
    "land": 15,
    "road": 25,
    "sea": 75,
}


def get_proportional_speed_mod(army_size: int) -> float:
    """
    Returns a smooth speed modifier based on army size.
    No steps/tiers. 10,001 men is only slightly slower than 9,999.
    """
    # 1. SCOUT PARTIES (< 1,000)
    # Scale: 0 men = 2.0x speed -> 1,000 men = 1.0x speed
    if army_size < 1000:
        # Avoid division by zero if army size is somehow 0
        safe_size = max(1, army_size)
        # Linear drop from 2.0 to 1.0
        return 2.0 - (safe_size / 1000.0)

    # 2. MAIN ARMIES (1,000 to 50,000)
    # Scale: 1,000 men = 1.0x speed -> 50,000 men = 0.2x speed
    elif army_size <= 50000:
        # Range of troops: 49,000 (from 1k to 50k)
        # Range of speed drop: 0.8 (from 1.0 down to 0.2)

        progress = (army_size - 1000) / 49000.0
        drop = progress * 0.8

        return 1.0 - drop

    # 3. MASSIVE HOSTS (> 50,000)
    # Cap at 0.2 (Crushing Pace)
    else:
        return 0.2


def calculate_travel_duration(terrain_breakdown_pixels, army_size):
    """
    Calculates journey duration using smooth proportional scaling.
    """
    # Get the smooth modifier
    speed_mod = get_proportional_speed_mod(army_size)

    total_game_days = 0

    for terrain_type, pixel_dist in terrain_breakdown_pixels.items():
        if pixel_dist <= 0:
            continue

        # Convert Pixels to Miles
        miles = pixel_dist / PIXELS_PER_MILE

        # Get Base Speed (Miles/Day)
        base_speed = BASE_SPEEDS.get(terrain_type, 15)

        # Formula: Miles / (BaseSpeed * Modifier)
        # Example: 25 miles / (25 * 0.5) = 2 Days
        days = miles / (base_speed * speed_mod)

        total_game_days += days

    # Convert to Real Seconds
    # Ensure REAL_SECONDS_PER_GAME_DAY is set to 2400 in config.py
    total_real_seconds = total_game_days * REAL_SECONDS_PER_GAME_DAY

    return int(total_real_seconds)
