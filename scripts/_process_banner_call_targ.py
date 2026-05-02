import os
import sys
from pathlib import Path

# Add project root to the Python path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import select
from app.db.sync_db import get_sync_session
from app.db.models import Game, House, User, GamePlayer

# --- CONFIGURATION ---
GUILD_ID_FOR_NEW_GAME = 987654321098765432
HOUSE_TO_CLAIM = "Targaryen"
PLAYER_DISCORD_ID = 123456789012345678
PLAYER_DISPLAY_NAME = "TestTargPlayer"
IS_PRIMARY_CLAIM = True
# --- END CONFIGURATION ---


# Helper for colored terminal output
class TColors:
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"


def print_success(text):
    print(f"{TColors.OKGREEN}✅ {text}{TColors.ENDC}")


def print_warning(text):
    print(f"{TColors.WARNING}⚠️ {text}{TColors.ENDC}")


def print_fail(text):
    print(f"{TColors.FAIL}❌ {text}{TColors.ENDC}")


def claim_house_sandboxed():
    """
    Creates a temporary game, claims a house within it, and cleans up afterwards.
    """
    print(
        f"\n{TColors.BOLD}--- Starting Sandboxed House Claim Script ---{TColors.ENDC}"
    )
    session = get_sync_session()

    new_game = None
    original_house_game_id = None
    house_to_claim = None

    try:
        # 1. SETUP: Create a new Game instance
        print("\n--- Phase 1: Creating Sandbox Game ---")
        new_game = Game(guild_id=GUILD_ID_FOR_NEW_GAME)
        new_game.status = "active"
        session.add(new_game)
        session.flush()
        print_success(f"Created temporary Game with ID: {new_game.game_id}")

        # 2. Find the House and temporarily "borrow" it
        print("\n--- Phase 2: Finding and Borrowing House ---")
        house_to_claim = (
            session.query(House).filter(House.name.ilike(HOUSE_TO_CLAIM)).first()
        )
        if not house_to_claim:
            print_fail(f"House '{HOUSE_TO_CLAIM}' not found in the database at all.")
            return

        original_house_game_id = house_to_claim.game_id
        print_success(
            f"Found House: {house_to_claim.name} (Original Game ID: {original_house_game_id})"
        )
        house_to_claim.game_id = new_game.game_id
        print_warning(
            f"Temporarily assigned House '{house_to_claim.name}' to new Game ID {new_game.game_id}."
        )

        # --- PHASE 3: FINDING OR CREATING USER (CORRECTED) ---
        print("\n--- Phase 3: Finding or Creating User ---")
        target_user = (
            session.query(User).filter(User.discord_id == PLAYER_DISCORD_ID).first()
        )
        if not target_user:
            print_warning(
                f"User with Discord ID {PLAYER_DISCORD_ID} not found. Creating new user..."
            )

            # OLD, BUGGY LINE:
            # target_user = User(discord_id=PLAYER_DISCORD_ID, display_name=PLAYER_DISPLAY_NAME)

            # NEW, CORRECTED LOGIC:
            # 1. Create the User instance with its required arguments.
            target_user = User(discord_id=PLAYER_DISCORD_ID)
            # 2. Set the other attributes.
            target_user.name = PLAYER_DISPLAY_NAME

            session.add(target_user)
            session.flush()
            print_success(
                f"Created new user: {target_user.name} (User ID: {target_user.user_id})"
            )
        else:
            print_success(
                f"Found existing user: {target_user.name} (User ID: {target_user.user_id})"
            )

        # ... (The rest of the script is correct and remains the same)

        # 4. Check for and Prevent Conflicting Claims
        existing_house_claim = (
            session.query(GamePlayer)
            .filter(GamePlayer.claimed_house_id == house_to_claim.house_id)
            .first()
        )
        if existing_house_claim:
            claimed_by_user = session.get(User, existing_house_claim.user_id)
            print_fail(
                f"House '{house_to_claim.name}' is already claimed by {claimed_by_user.name}. Please `!vacate` them first."
            )
            return

        # 5. Create the GamePlayer Claim
        print("\n--- Phase 4: Creating GamePlayer Claim ---")
        new_player_claim = GamePlayer(
            user_id=target_user.user_id,
            game_id=new_game.game_id,
            claimed_house_id=house_to_claim.house_id,
            is_primary=IS_PRIMARY_CLAIM,
        )
        session.add(new_player_claim)
        session.commit()
        print_success(
            f"Successfully created claim for {target_user.display_name} on House {house_to_claim.name} in temporary Game {new_game.game_id}!"
        )

    except Exception as e:
        print_fail(f"\nAn unexpected error occurred during setup: {e}")
        session.rollback()

    finally:
        # 6. TEARDOWN
        print("\n--- Phase 5: Cleaning Up Sandbox ---")
        if new_game:
            game_to_delete = (
                session.query(Game).filter(Game.game_id == new_game.game_id).first()
            )
            if game_to_delete:
                claim_to_delete = (
                    session.query(GamePlayer)
                    .filter(GamePlayer.game_id == new_game.game_id)
                    .first()
                )
                if claim_to_delete:
                    session.delete(claim_to_delete)
                    print_warning(
                        f"Deleting GamePlayer claim (ID: {claim_to_delete.player_id})..."
                    )
                session.delete(game_to_delete)
                print_warning(
                    f"Deleting temporary Game (ID: {game_to_delete.game_id})..."
                )
        if house_to_claim:
            house_to_claim.game_id = original_house_game_id
            print_warning(
                f"Restoring House '{house_to_claim.name}' to original Game ID ({original_house_game_id})..."
            )
        session.commit()
        print_success("Cleanup complete. Database is back to its original state.")
        session.close()
        print(f"\n{TColors.BOLD}--- Sandboxed Script Finished ---{TColors.ENDC}")


if __name__ == "__main__":
    claim_house_sandboxed()
