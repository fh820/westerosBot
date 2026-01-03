# app/tasks/battle_tasks.py

from app.celery_app import celery_app
from app.db.db_manager import get_session, engine
from app.db.models import PendingInteraction, Battle, Army, GamePlayer, User
from app.services.battle_service import BattleService
from sqlalchemy.orm import selectinload
from sqlalchemy import select
import json
import redis
import os
import datetime
import asyncio

REDIS_CLIENT = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


async def _publish_to_redis_async(channel: str, message: str):
    await asyncio.to_thread(REDIS_CLIENT.publish, channel, message)


async def _initiate_auto_battle_logic(interaction_id: int):
    print(f"--- Initiating Auto-Battle for Interaction ID {interaction_id} ---")
    try:
        async with get_session() as session:
            interaction = (
                (
                    await session.execute(
                        select(PendingInteraction)
                        .options(
                            selectinload(PendingInteraction.army1).selectinload(
                                Army.game
                            ),
                            selectinload(PendingInteraction.army1).selectinload(
                                Army.house
                            ),
                            selectinload(PendingInteraction.army2).selectinload(
                                Army.house
                            ),
                        )
                        .filter(PendingInteraction.id == interaction_id)
                    )
                )
                .scalars()
                .first()
            )

            if not interaction or not interaction.army1 or not interaction.army2:
                print("Auto-battle cancelled: Interaction or armies not found.")
                return

            service = BattleService(session)
            # Signature check: start_auto_battle returns (battle, odds_msg, calc_log)
            battle, _, _ = await service.start_auto_battle(
                game_id=interaction.game_id,
                attacker_id=interaction.army1_id,
                defender_id=interaction.army2_id,
                ambush="none",
                defense="none",
            )

            if not battle:
                print("Failed to create battle record.")
                return

            # Publish Public Start Event
            start_payload = {
                "type": "BATTLE_STARTED",
                "guild_id": battle.game.guild_id,
                "battle_id": battle.id,
                "attacker_name": interaction.army1.commander_name,
                "defender_name": interaction.army2.commander_name,
                "attacker_house": interaction.army1.house.name,
                "defender_house": interaction.army2.house.name,
            }
            await _publish_to_redis_async(
                "westeros_bot_events", json.dumps(start_payload)
            )

            # 15-minute grace period for GMs to intervene manually
            grace_period_end = datetime.datetime.now(
                datetime.timezone.utc
            ) + datetime.timedelta(minutes=15)
            first_round_task = run_auto_battle_round.apply_async(
                args=[battle.id, 1], eta=grace_period_end
            )

            # Publish GM Prompt
            payload = {
                "type": "PROMPT_AUTOBATTLE",
                "guild_id": battle.game.guild_id,
                "battle_id": battle.id,
                "attacker_name": interaction.army1.commander_name,
                "defender_name": interaction.army2.commander_name,
                "resolver_task_id": first_round_task.id,
            }
            await _publish_to_redis_async("westeros_bot_events", json.dumps(payload))

    except Exception as e:
        print(f"Error in initiate_auto_battle: {e}")
        raise
    finally:
        await engine.dispose()


@celery_app.task(acks_late=True, name="initiate_auto_battle")
def initiate_auto_battle(interaction_id: int):
    asyncio.run(_initiate_auto_battle_logic(interaction_id))


# --- TASK 2: RUN ROUND (Unchanged, as process_auto_battle_round signature is fine) ---


async def _run_auto_battle_round_logic(battle_id: int, round_number: int):
    try:
        async with get_session() as session:
            service = BattleService(session)
            battle, roll_msg, winner, _ = await service.process_auto_battle_round(
                battle_id
            )

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
            await _publish_to_redis_async("westeros_bot_events", json.dumps(payload))

            if winner:
                resolve_battle_aftermath.apply_async(args=[battle_id], countdown=10)
            else:
                next_round = datetime.datetime.now(
                    datetime.timezone.utc
                ) + datetime.timedelta(minutes=1)
                run_auto_battle_round.apply_async(
                    args=[battle.id, round_number + 1], eta=next_round
                )
    except Exception as e:
        print(f"Error in run_auto_battle_round: {e}")
        raise
    finally:
        await engine.dispose()


@celery_app.task(acks_late=True, name="run_auto_battle_round")
def run_auto_battle_round(battle_id: int, round_number: int):
    asyncio.run(_run_auto_battle_round_logic(battle_id, round_number))


# --- TASK 3: RESOLVE AFTERMATH (CRITICAL UPDATES) ---


async def _resolve_battle_aftermath_logic(battle_id: int):
    print(f"--- Resolving Aftermath for Battle ID {battle_id} ---")
    try:
        async with get_session() as session:
            service = BattleService(session)

            # THE FIX: Unpack 3 values to match our updated Service signature
            final_report, guild_id, notif_data = (
                await service.resolve_auto_battle_aftermath(battle_id)
            )

            if not final_report or not guild_id:
                return

            # Publish Final Report and include the notification data for the Cog
            payload = {
                "type": "BATTLE_REPORT_FINAL",
                "guild_id": guild_id,
                "battle_id": battle_id,
                "report_string": final_report,
                # Add notification metadata for Locked Quarters
                "loser_discord_id": notif_data.get("loser_discord_id"),
                "loser_channel_id": notif_data.get("loser_channel_id"),
                "is_retreat": notif_data.get("is_retreat", False),
            }
            await _publish_to_redis_async("westeros_bot_events", json.dumps(payload))

    except Exception as e:
        print(f"Error in resolve_battle_aftermath: {e}")
        raise
    finally:
        await engine.dispose()


@celery_app.task(acks_late=True, name="resolve_battle_aftermath")
def resolve_battle_aftermath(battle_id: int):
    asyncio.run(_resolve_battle_aftermath_logic(battle_id))
