import datetime
from sqlalchemy import (
    Column,
    BigInteger,
    Integer,
    String,
    Boolean,
    Float,
    ForeignKey,
    DateTime,
    JSON,
)
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
import sqlalchemy as sa


Base = declarative_base()


class Game(Base):
    __tablename__ = "games"

    game_id = Column(Integer, primary_key=True, autoincrement=True)
    guild_id = Column(BigInteger, nullable=False, index=True)
    upkeep_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sa.true()
    )
    manpower_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sa.true()
    )
    name = Column(String, default="Westeros")
    current_year = Column(Integer, default=298)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    ruling_house = Column(String, default="Baratheon")
    twins_open: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sa.true()
    )
    rubyford_open: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sa.true()
    )
    bitterbridge_open: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sa.true()
    )
    rivers_impassable: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sa.true()
    )
    sea_travel_allowed: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=sa.true()
    )
    ship_capacity = Column(Integer, default=100, nullable=False)
    # Relationships
    houses = relationship("House", back_populates="game", cascade="all, delete-orphan")
    fiefs = relationship(
        "Fief", back_populates="game", cascade="all, delete-orphan"
    )  # NEW
    armies = relationship("Army", back_populates="game", cascade="all, delete-orphan")
    players = relationship(
        "GamePlayer", back_populates="game", cascade="all, delete-orphan"
    )
    income_modifiers = Column(JSON, default={}, server_default="{}")


class User(Base):
    __tablename__ = "users"

    user_id = Column(BigInteger, primary_key=True, autoincrement=True)
    discord_id = Column(BigInteger, unique=True, nullable=True)
    is_npc = Column(Boolean, default=False)
    is_gm = Column(Boolean, default=False, server_default=sa.false())
    players = relationship("GamePlayer", back_populates="user")


class GamePlayer(Base):
    __tablename__ = "game_players"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id"))
    user_id = Column(BigInteger, ForeignKey("users.user_id"))
    private_channel_id = Column(BigInteger, nullable=True)  # <--- ADD THIS
    claimed_house_id = Column(Integer, ForeignKey("houses.house_id"), nullable=True)

    character_id = Column(Integer, ForeignKey("characters.char_id"), nullable=True)

    is_primary = Column(Boolean, default=True)

    # Relationships
    game = relationship("Game", back_populates="players")
    user = relationship("User", back_populates="players")
    house = relationship("House")

    character = relationship("Character")


# ... other imports
from sqlalchemy.dialects.postgresql import JSONB

# ... after your other model classes


class PendingBannerCall(Base):
    """
    Stores the state of a banner call that is awaiting GM approval and adjustment.
    """

    __tablename__ = "pending_banner_calls"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False)
    call_type = Column(String, default="LAND", nullable=False)
    # Information to find and update the original player's message
    guild_id = Column(BigInteger, nullable=False)
    channel_id = Column(BigInteger, nullable=False)
    message_id = Column(BigInteger, nullable=False)

    # Information to find and update the GM control panel
    gm_channel_id = Column(BigInteger, nullable=False)
    gm_message_id = Column(BigInteger, nullable=False)

    # Core data for the muster
    liege_house_id = Column(Integer, ForeignKey("houses.house_id"), nullable=False)
    rally_point_name = Column(String, nullable=False)

    # The adjustable data for each vassal
    # Format: [{"house_id": int, "house_name": str, "max_troops": int, "percent": float}, ...]
    vassal_data = Column(JSONB, nullable=False)

    # Status tracking
    status = Column(
        String, default="PENDING_APPROVAL", nullable=False
    )  # PENDING_APPROVAL, COMPLETED, CANCELLED
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    liege_house = relationship("House")


class House(Base):
    """
    Represents the Faction / Family.
    Example: House Stark.
    """

    __tablename__ = "houses"

    house_id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False)

    # The two conflicting columns
    dynasty_id = Column(Integer, ForeignKey("houses.house_id"), nullable=True)
    liege_id = Column(Integer, ForeignKey("houses.house_id"), nullable=True)

    name = Column(String, nullable=False)  # "Stark"
    is_ruined = Column(Boolean, default=False, nullable=False)
    # Mechanics
    house_type = Column(String, default="feudal")  # feudal, clan, mercenary
    treasury = Column(BigInteger, default=0)
    paying_taxes = Column(Boolean, default=True)

    # Flavor
    ancestral_weapon = Column(String, nullable=True)
    color_hex = Column(String, default="#FFFFFF")

    # Relationships
    game = relationship("Game", back_populates="houses")
    fiefs = relationship("Fief", back_populates="owner")
    characters = relationship(
        "Character", back_populates="house", cascade="all, delete-orphan"
    )
    armies = relationship("Army", back_populates="house")

    # We must add foreign_keys=[liege_id] to tell SQLALchemy exactly which column to use
    liege = relationship(
        "House", remote_side=[house_id], foreign_keys=[liege_id], backref="vassals"
    )

    # We also add a backref to dynasty for convenience
    dynasty = relationship(
        "House", remote_side=[house_id], foreign_keys=[dynasty_id], backref="scions"
    )

    manpower = Column(Integer, default=0)  # Current available recruits
    manpower_cap = Column(Integer, default=0)  # Max recruits based on land
    tax_rate = Column(Float, default=0.10)  # Default 10% (0.10)
    gate_whitelist = Column(JSON, default=[])


class Fief(Base):
    """
    Represents the Property (Castle/City/Ruin) on the map.
    Example: Winterfell (Owned by House Stark).
    """

    __tablename__ = "fiefs"

    fief_id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id"))

    # Ownership (Mutable - changes during conquest)
    owner_id = Column(Integer, ForeignKey("houses.house_id"), nullable=True)

    name = Column(String, nullable=False)  # "Winterfell"
    region = Column(String)  # "The North"

    # Mechanics
    fief_type = Column(String, default="castle")  # castle, city, camp, ruin
    is_ruined = Column(Boolean, default=False)
    base_income = Column(BigInteger, default=0)

    # Coordinates
    location_x = Column(Float)
    location_y = Column(Float)

    # Relationships
    game = relationship("Game", back_populates="fiefs")
    owner = relationship("House", back_populates="fiefs")
    integration = Column(Float, default=1.0)
    base_manpower = Column(Integer, default=0)


class Character(Base):
    """
    RPG Character (Head of House, Heirs, Knights).
    """

    __tablename__ = "characters"

    char_id = Column(Integer, primary_key=True, autoincrement=True)
    house_id = Column(Integer, ForeignKey("houses.house_id"))

    name = Column(String, nullable=False)
    is_head = Column(Boolean, default=False)

    # Skills: {"martial": 10, "intrigue": 5...}
    skills = Column(JSON, default={})

    house = relationship("House", back_populates="characters")
    spouse_id = Column(Integer, ForeignKey("characters.char_id"), nullable=True)
    spouse = relationship("Character", remote_side=[char_id], uselist=False)


class ArmyContingent(Base):
    """
    Stores a piece of a Coalition Army.
    Example: The Stark contribution to the Grand Host of the Trident.
    """

    __tablename__ = "army_contingents"

    id = Column(Integer, primary_key=True)

    # Which Coalition does this belong to?
    parent_army_id = Column(Integer, ForeignKey("armies.army_id"))

    # Who originally owned these troops?
    original_house_id = Column(Integer, ForeignKey("houses.house_id"))

    # What did they contribute?
    troop_count = Column(Integer)
    composition = Column(JSON, default={})

    cargo = Column(JSON, nullable=True)
    # Relationships
    parent_army = relationship("Army", back_populates="contingents")
    original_house = relationship("House", foreign_keys=[original_house_id])
    treasury = Column(Integer, default=0)


class Army(Base):
    """
    Military Units.
    """

    __tablename__ = "armies"

    army_id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id"))
    house_id = Column(Integer, ForeignKey("houses.house_id"))

    commander_name = Column(String)

    # Total count (cached sum)
    troop_count = Column(Integer, default=0)

    # Detailed Breakdown
    composition = Column(JSON, default={})

    # Location / Movement
    location_x = Column(Float)
    location_y = Column(Float)
    destination_x = Column(Float, nullable=True)
    destination_y = Column(Float, nullable=True)
    arrival_time = Column(DateTime(timezone=True), nullable=True)

    status = Column(String, default="IDLE")
    is_coalition = Column(Boolean, default=False)

    arrival_time: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- ADD THESE TWO NEW COLUMNS ---
    departure_time: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    task_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )  # To store the Celery task ID

    # NEW: Relationship to its pieces
    contingents = relationship(
        "ArmyContingent", back_populates="parent_army", cascade="all, delete-orphan"
    )
    game = relationship("Game", back_populates="armies")
    house = relationship("House", back_populates="armies")
    army_type = Column(String, default="LAND")  # Values: "LAND", "SEA"
    cargo = Column(JSON, nullable=True)
    treasury = Column(BigInteger, default=0)
    # --- ADD THESE NEW COLUMNS ---
    original_destination_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    original_destination_y: Mapped[float | None] = mapped_column(Float, nullable=True)


class MarchLog(Base):
    """
    Stores checkpoints of a marching army to detect collisions.
    Rows are created when a march starts and deleted when it ends/stops.
    """

    __tablename__ = "march_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), index=True)
    army_id = Column(Integer, ForeignKey("armies.army_id"), index=True)

    # Where will they be?
    x = Column(Float, index=True)
    y = Column(Float, index=True)

    # When will they be there? (UTC)
    estimated_time = Column(DateTime(timezone=True), index=True)


class Battle(Base):
    """Stores the state of an active battle."""

    __tablename__ = "battles"

    id = Column(Integer, primary_key=True)
    game_id = Column(Integer, ForeignKey("games.game_id"))

    attacker_id = Column(Integer, ForeignKey("armies.army_id"))
    defender_id = Column(Integer, ForeignKey("armies.army_id"))

    attacker_score = Column(Integer, default=0)
    defender_score = Column(Integer, default=0)

    current_odds = Column(
        Integer, default=50
    )  # The number the attacker needs to roll under

    # Store the channel/message IDs so the bot can edit them
    public_channel_id = Column(BigInteger, nullable=True)
    public_message_id = Column(BigInteger, nullable=True)
    gm_channel_id = Column(BigInteger, nullable=True)
    gm_message_id = Column(BigInteger, nullable=True)
    att_start_cargo_count = Column(Integer, default=0)
    def_start_cargo_count = Column(Integer, default=0)
    # Add relationships to get army names easily
    attacker = relationship("Army", foreign_keys=[attacker_id])
    defender = relationship("Army", foreign_keys=[defender_id])

    battle_type = Column(String, default="FIELD")  # "FIELD" or "SIEGE"
    siege_phase = Column(String, nullable=True)  # "WALLS" or "STREETS"
    att_start_count = Column(Integer, default=0)
    def_start_count = Column(Integer, default=0)
    # Store the Fief being sieged
    fief_id = Column(Integer, ForeignKey("fiefs.fief_id"), nullable=True)

    # Relationships
    fief = relationship("Fief")
    game = relationship("Game")
    winner_id = Column(Integer, nullable=True)


class PendingInteraction(Base):
    """
    Stores the state of a potential army interception that requires player choices.
    """

    __tablename__ = "pending_interactions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    game_id = Column(Integer, ForeignKey("games.game_id"), nullable=False)

    # --- The Armies Involved ---
    # Army 1 is always the one that INITIATED the movement (the marcher)
    army1_id = Column(Integer, ForeignKey("armies.army_id"), nullable=False)
    # Army 2 is the target army (can be idle or also moving)
    army2_id = Column(Integer, ForeignKey("armies.army_id"), nullable=False)

    # --- Player Choices ---
    # Stores "BATTLE", "MEETING", or "MARCH_ON"
    army1_choice = Column(String, nullable=True)
    army2_choice = Column(String, nullable=True)

    # --- State Management ---
    # PENDING, RESOLVED_BATTLE, RESOLVED_MEETING, RESOLVED_MARCH_ON, EXPIRED, CANCELLED_GM
    status = Column(String, default="PENDING", nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # The exact time the players' decision window closes.
    expires_at = Column(DateTime(timezone=True), nullable=False)

    # --- Location & Messaging ---
    # Store the location of the intercept to halt armies correctly.
    location_x = Column(Float, nullable=False)
    location_y = Column(Float, nullable=False)

    # Store message/channel IDs to update the UI
    army1_channel_id = Column(BigInteger, nullable=True)
    army1_message_id = Column(BigInteger, nullable=True)
    army2_channel_id = Column(BigInteger, nullable=True)
    army2_message_id = Column(BigInteger, nullable=True)

    # Celery Task ID for the resolver, so we can revoke it if needed.
    resolver_task_id = Column(String, nullable=True)

    # Relationships for easy data access
    army1 = relationship("Army", foreign_keys=[army1_id])
    army2 = relationship("Army", foreign_keys=[army2_id])
