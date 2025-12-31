# In app/tasks/battle_tasks.py

from app.celery_app import celery_app
from app.db.sync_db import get_sync_session
from app.db.models import PendingInteraction, Battle, Army
from app.services.battle_service import BattleService
from sqlalchemy.orm import selectinload
import json
import redis
import os
import datetime

REDIS_CLIENT = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


@celery_app.task
def initiate_auto_battle(interaction_id: int):
    """
    1. Creates the formal Battle record.
    2. Notifies GMs and gives them a 15-minute grace period.
    3. Schedules the first round of the auto-battle to run after 15 mins.
    """
    session = get_sync_session()
    try:
        # =========================================================
        # ===== START: THE FIX IS IN THIS DATABASE QUERY      =====
        # =========================================================
        interaction = (
            session.query(PendingInteraction)
            .options(
                # We need to load Army -> Game relationship to get the guild_id
                selectinload(PendingInteraction.army1).selectinload(Army.game),
                selectinload(PendingInteraction.army2),
            )
            .filter(PendingInteraction.id == interaction_id)
            .first()
        )
        # =========================================================
        # ===== END: THE FIX                                  =====
        # =========================================================

        if not interaction or not interaction.army1 or not interaction.army2:
            print(
                f"Auto-battle cancelled: Interaction {interaction_id} or armies not found."
            )
            return

        # 1. Use the BattleService to create the Battle in the DB
        service = BattleService(session)
        battle, _, _ = service.start_battle_sync(
            game_id=interaction.game_id,
            attacker_id=interaction.army1_id,
            defender_id=interaction.army2_id,
            ambush="none",
            defense="none",
        )
        if not battle:
            print(f"Failed to create battle record for interaction {interaction_id}.")
            return

        start_payload = {
            "type": "BATTLE_STARTED",
            "guild_id": interaction.army1.game.guild_id,
            "battle_id": battle.id,
            "attacker_name": interaction.army1.commander_name,
            "defender_name": interaction.army2.commander_name,
            "attacker_house": interaction.army1.house.name,
            "defender_house": interaction.army2.house.name,
        }
        REDIS_CLIENT.publish("westeros_bot_events", json.dumps(start_payload))

        # 2. Schedule the first round to run after the grace period
        grace_period_end = datetime.datetime.now(
            datetime.timezone.utc
        ) + datetime.timedelta(minutes=15)
        first_round_task = run_auto_battle_round.apply_async(
            args=[battle.id, 1], eta=grace_period_end
        )

        # 3. Publish event to Redis to tell the bot to post the GM UI
        payload = {
            "type": "PROMPT_AUTOBATTLE",
            "guild_id": interaction.army1.game.guild_id,
            "battle_id": battle.id,
            "attacker_name": interaction.army1.commander_name,
            "defender_name": interaction.army2.commander_name,
            "resolver_task_id": first_round_task.id,
        }
        REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))
        print(f"DEBUG: Auto-battle {battle.id} initiated. GM grace period started.")

    finally:
        session.close()


@celery_app.task
def run_auto_battle_round(battle_id: int, round_number: int):
    """
    Processes a single round, posts a simplified score update, and schedules the next step.
    This version correctly handles the "first to 5" logic.
    """
    session = get_sync_session()
    try:
        print(
            f"--- Running Auto-Battle Round {round_number} for Battle ID {battle_id} ---"
        )
        service = BattleService(session)

        battle, roll_msg, winner, _ = service.process_battle_round_sync(battle_id)
        if not battle:
            return

        payload = {
            "type": "BATTLE_REPORT_ROUND",
            "guild_id": battle.game.guild_id,
            "battle_id": battle.id,
            "round_number": round_number,
            "scores": {
                "attacker": battle.attacker_score,
                "defender": battle.defender_score,
            },
            "roll_msg": roll_msg,
        }
        REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))

        # ===== START: THE "FIRST TO 5" FIX =====
        if winner:
            print(f"Battle {battle_id} concluded with a winner. Scheduling aftermath.")
            from .battle_tasks import resolve_battle_aftermath

            resolve_battle_aftermath.apply_async(args=[battle_id], countdown=10)
        # ===== END: THE "FIRST TO 5" FIX =====
        else:
            # The battle continues, schedule the next round
            next_round_time = datetime.datetime.now(
                datetime.timezone.utc
            ) + datetime.timedelta(minutes=1)
            run_auto_battle_round.apply_async(
                args=[battle_id, round_number + 1], eta=next_round_time
            )

    finally:
        session.close()


@celery_app.task
def resolve_battle_aftermath(battle_id: int):
    """
    Calls the service to resolve aftermath and publishes the final report.
    This version correctly handles the return values from the service.
    """
    print(f"--- Resolving Aftermath for Battle ID {battle_id} ---")
    session = get_sync_session()
    try:
        service = BattleService(session)
        # --- THE FIX: Unpack both the report string AND the guild_id ---
        final_report_string, guild_id = service.resolve_aftermath_sync(battle_id)

        # Check if the service returned valid data
        if not final_report_string or not guild_id:
            print(f"Aftermath for battle {battle_id} failed; service returned None.")
            return

        # --- THE FIX: Use the guild_id returned from the service ---
        payload = {
            "type": "BATTLE_REPORT_FINAL",
            "guild_id": guild_id,
            "battle_id": battle_id,
            "report_string": final_report_string,
        }
        REDIS_CLIENT.publish("westeros_bot_events", json.dumps(payload))

    finally:
        session.close()
