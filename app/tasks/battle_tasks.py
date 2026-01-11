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
from sqlalchemy import update

REDIS_CLIENT = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))


async def _publish_to_redis_async(channel: str, message: str):
    await asyncio.to_thread(REDIS_CLIENT.publish, channel, message)


async def _initiate_auto_battle_logic(interaction_id: int):
    print(f"--- Initiating Auto-Battle for Interaction ID {interaction_id} ---")
    try:
        async with get_session() as session:
            # Start a transaction to ensure atomicity
            await session.begin()

            # =============================================================
            # THE IDEMPOTENCY FIX: Atomic Status Update
            # We try to "claim" this task by updating the status from
            # 'RESOLVED_BATTLE' to 'BATTLE_INITIATED'.
            # =============================================================
            stmt = (
                update(PendingInteraction)
                .where(
                    PendingInteraction.id == interaction_id,
                    PendingInteraction.status
                    == "RESOLVED_BATTLE",  # Only works if currently in this state
                )
                .values(
                    status="BATTLE_INITIATED"
                )  # Change to intermediate processing state
                .returning(PendingInteraction.id)  # Return ID only if update succeeded
            )

            # Execute the update
            result = await session.execute(stmt)
            claimed_id = result.scalar_one_or_none()

            # CRITICAL CHECK:
            # If claimed_id is None, it means the UPDATE matched 0 rows.
            # This happens if another worker already claimed it or status is wrong.
            if not claimed_id:
                print(
                    f"Skipping Auto-Battle for {interaction_id}: Already initiated or invalid status."
                )
                await session.commit()  # Close transaction safely
                return

            # =============================================================
            # We "won the race". Proceed with logic.
            # =============================================================

            # 1. Fetch the full interaction data now that we own the task
            interaction = await session.get(
                PendingInteraction,
                interaction_id,
                options=[
                    selectinload(PendingInteraction.army1).options(
                        selectinload(Army.game), selectinload(Army.house)
                    ),
                    selectinload(PendingInteraction.army2).selectinload(Army.house),
                ],
            )

            if not interaction or not interaction.army1 or not interaction.army2:
                print("Auto-battle cancelled: Interaction or armies not found.")
                await session.rollback()  # Revert status change so it can be debugged
                return

            # 2. Start the Battle Service
            service = BattleService(session)
            battle, _, _ = await service.start_auto_battle(
                game_id=interaction.game_id,
                attacker_id=interaction.army1_id,
                defender_id=interaction.army2_id,
                ambush="none",
                defense="none",
            )

            if not battle:
                print("Failed to create battle record.")
                await session.rollback()  # Revert status change
                return

            # 3. Publish Public Start Event
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

            # 4. Schedule First Round (15-minute grace period)
            grace_period_end = datetime.datetime.now(
                datetime.timezone.utc
            ) + datetime.timedelta(minutes=15)

            first_round_task = run_auto_battle_round.apply_async(
                args=[battle.id, 1], eta=grace_period_end
            )

            # 5. Publish GM Prompt
            payload = {
                "type": "PROMPT_AUTOBATTLE",
                "guild_id": battle.game.guild_id,
                "battle_id": battle.id,
                "attacker_name": interaction.army1.commander_name,
                "defender_name": interaction.army2.commander_name,
                "resolver_task_id": first_round_task.id,
            }
            await _publish_to_redis_async("westeros_bot_events", json.dumps(payload))

            # 6. Success! Commit transaction.
            await session.commit()

    except Exception as e:
        print(f"Error in initiate_auto_battle: {e}")
        # Only rollback if the session is still active and valid
        if "session" in locals() and session.in_transaction():
            await session.rollback()
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
            # Start a transaction to hold the lock
            await session.begin()

            # =============================================================
            # THE IDEMPOTENCY FIX: Lock the Battle Record
            # =============================================================
            battle_locked = (
                (
                    await session.execute(
                        select(Battle)
                        .filter(Battle.id == battle_id)
                        .with_for_update()  # The Lock
                    )
                )
                .scalars()
                .first()
            )

            # 1. Check if Battle exists or is already finished
            # If winner_id is set, the battle is over. Ignore this task.
            if not battle_locked or battle_locked.winner_id is not None:
                # If we tracked 'current_round' in DB, we would also check:
                # if battle_locked.current_round >= round_number: return
                print(
                    f"Skipping round {round_number} for Battle {battle_id}: Battle invalid or already finished."
                )
                await session.commit()
                return

            service = BattleService(session)

            # Pass the ID (the service will use the existing session and see the locked row)
            battle, roll_msg, winner, _ = await service.process_auto_battle_round(
                battle_locked.id
            )

            if not battle:
                await session.rollback()
                return

            # Publish Round Report
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

            # Schedule Next Step
            if winner:
                resolve_battle_aftermath.apply_async(args=[battle_id], countdown=10)
            else:
                next_round = datetime.datetime.now(
                    datetime.timezone.utc
                ) + datetime.timedelta(minutes=1)
                run_auto_battle_round.apply_async(
                    args=[battle.id, round_number + 1], eta=next_round
                )

            # Commit the transaction to release the lock and save the round results
            await session.commit()

    except Exception as e:
        print(f"Error in run_auto_battle_round: {e}")
        # Only rollback if the session is still active
        if "session" in locals() and session.in_transaction():
            await session.rollback()
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
            # Start transaction
            await session.begin()

            # =============================================================
            # THE IDEMPOTENCY FIX: Lock the Battle Record
            # =============================================================
            # We attempt to lock the battle row.
            # 1. If another worker is running this, we wait here.
            # 2. If the previous worker finished and deleted the battle, this returns None.
            battle_locked = (
                (
                    await session.execute(
                        select(Battle).filter(Battle.id == battle_id).with_for_update()
                    )
                )
                .scalars()
                .first()
            )

            if not battle_locked:
                print(
                    f"Battle {battle_id} not found or already resolved. Skipping duplicate aftermath task."
                )
                await session.commit()
                return

            service = BattleService(session)

            # THE FIX: Unpack 3 values to match our updated Service signature
            # The service uses the session that holds the lock.
            final_report, guild_id, notif_data = (
                await service.resolve_auto_battle_aftermath(battle_id)
            )

            if not final_report or not guild_id:
                # If service returned failure/empty, rollback changes
                await session.rollback()
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

            # Commit the transaction (this deletes the battle/updates armies and releases the lock)
            await session.commit()

    except Exception as e:
        print(f"Error in resolve_battle_aftermath: {e}")
        # Only rollback if the session is still active
        if "session" in locals() and session.in_transaction():
            await session.rollback()
        raise
    finally:
        await engine.dispose()


@celery_app.task(acks_late=True, name="resolve_battle_aftermath")
def resolve_battle_aftermath(battle_id: int):
    asyncio.run(_resolve_battle_aftermath_logic(battle_id))
