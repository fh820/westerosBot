import datetime
import random

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.db.models import Army, Battle, Fief, GamePlayer, ScoutReport, User


CONFIDENCE_TIERS = (
    (80, "excellent"),
    (60, "good"),
    (40, "basic"),
    (20, "poor"),
    (-999, "failed"),
)

SIZE_BANDS = (
    (0, 99, "scattered handful"),
    (100, 499, "small force"),
    (500, 1999, "modest host"),
    (2000, 5999, "large host"),
    (6000, 14999, "great host"),
    (15000, 99999999, "massive host"),
)

LAND_TERRAIN_SCOUT_MODIFIERS = {
    "unknown": 0,
    "plains": 12,
    "hills": 3,
    "forest": -12,
    "mountains": -10,
    "river": 6,
    "marsh": -8,
    "urban": -8,
    "coast": 3,
}

SEA_TERRAIN_SCOUT_MODIFIERS = {
    "unknown": 0,
    "open_sea": 10,
    "coast": 6,
    "strait": -3,
    "storm": -18,
}

PLAN_CONCEALMENT = {
    "ambush": 20,
    "feint": 12,
    "cautious": 8,
    "reserve": 7,
    "flank": 5,
    "defensive": 0,
    "aggressive": -5,
}


class ScoutingService:
    def __init__(self, session):
        self.session = session

    async def _get_player_house_id(self, game_id: int, discord_id: int):
        stmt = (
            select(GamePlayer.claimed_house_id)
            .join(User, User.user_id == GamePlayer.user_id)
            .where(User.discord_id == discord_id, GamePlayer.game_id == game_id)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def _get_army_martial(self, army: Army) -> int:
        if army.commander_martial is not None:
            return army.commander_martial

        stmt = (
            select(GamePlayer)
            .where(
                GamePlayer.game_id == army.game_id,
                GamePlayer.claimed_house_id == army.house_id,
                GamePlayer.is_primary == True,
            )
            .options(selectinload(GamePlayer.character))
        )
        player = (await self.session.execute(stmt)).scalars().first()
        if player and player.character and player.character.skills:
            return player.character.skills.get("martial", 0)
        return 0

    def _distance(self, a_x, a_y, b_x, b_y):
        return (((a_x or 0) - (b_x or 0)) ** 2 + ((a_y or 0) - (b_y or 0)) ** 2) ** 0.5

    def _composition_count(self, army: Army, *unit_names):
        if not army or not army.composition:
            return 0
        wanted = {name.lower() for name in unit_names}
        return sum(
            count
            for unit, count in army.composition.items()
            if unit.lower() in wanted
        )

    def _mobility_bonus(self, army: Army):
        total = max(army.troop_count or 0, 1)
        if army.army_type == "SEA":
            return min(20, max(0, int((army.troop_count or 0) / 10)))
        cavalry = self._composition_count(army, "knights", "cavalry")
        return int(min(20, (cavalry / total) * 35))

    def _scout_range(self, army: Army, martial: int):
        if army.army_type == "SEA":
            return 350 + min(150, (army.troop_count or 0) * 2) + martial * 5
        return 220 + self._mobility_bonus(army) * 5 + martial * 5

    async def _nearest_terrain(self, game_id: int, x: float, y: float, army_type: str):
        stmt = select(Fief).where(Fief.game_id == game_id)
        fiefs = (await self.session.execute(stmt)).scalars().all()
        if not fiefs:
            return "open_sea" if army_type == "SEA" else "unknown"
        nearest = min(
            fiefs,
            key=lambda f: self._distance(x, y, f.location_x, f.location_y),
        )
        if army_type == "SEA":
            return "coast" if self._distance(x, y, nearest.location_x, nearest.location_y) < 180 else "open_sea"
        region = (nearest.region or "").lower()
        if "mountain" in region or "vale" in region:
            return "mountains"
        if "neck" in region or "crannog" in region:
            return "marsh"
        if "river" in region or "trident" in region:
            return "river"
        if "storm" in region or "north" in region:
            return "forest"
        if "crown" in region or "king" in nearest.name.lower():
            return "urban"
        return "plains"

    async def _target_plan(self, target: Army):
        stmt = select(Battle).where(
            or_(Battle.attacker_id == target.army_id, Battle.defender_id == target.army_id)
        )
        battle = (await self.session.execute(stmt)).scalars().first()
        if not battle:
            return None
        if battle.attacker_id == target.army_id:
            return battle.attacker_plan
        return battle.defender_plan

    def _confidence_for_score(self, score: int):
        for threshold, label in CONFIDENCE_TIERS:
            if score >= threshold:
                return label
        return "failed"

    def _size_band(self, count: int, confidence: str, misleading=False):
        if misleading:
            count = max(0, int(count * random.choice([0.45, 1.8])))
        for low, high, label in SIZE_BANDS:
            if low <= count <= high:
                return label
        return "unknown force"

    def _count_band(self, count: int, confidence: str, misleading=False):
        if confidence in ("failed", "poor"):
            return "unclear"
        if misleading:
            count = max(0, int(count * random.choice([0.5, 1.7])))
        fuzz = {
            "basic": 0.45,
            "good": 0.30,
            "excellent": 0.18,
        }.get(confidence, 0.60)
        low = max(0, int(count * (1 - fuzz)))
        high = max(low + 1, int(count * (1 + fuzz)))
        return f"{low}-{high}"

    def _composition_summary(self, army: Army, confidence: str, misleading=False):
        if confidence in ("failed", "poor"):
            return {"summary": "unclear"}
        comp = army.composition or {}
        total = max(sum(comp.values()), 1)
        groups = {
            "infantry": comp.get("infantry", 0) + comp.get("militia", 0),
            "archers": comp.get("archers", 0),
            "cavalry": comp.get("cavalry", 0) + comp.get("knights", 0),
            "ships": comp.get("ships", 0) + comp.get("ship", 0) + comp.get("warships", 0),
        }
        if misleading:
            flip = random.choice(list(groups.keys()))
            groups[flip] = int(groups[flip] * random.choice([0.2, 2.0]))

        labels = {}
        for group, count in groups.items():
            ratio = count / total
            if count <= 0:
                labels[group] = "none"
            elif ratio < 0.12:
                labels[group] = "few"
            elif ratio < 0.35:
                labels[group] = "some"
            else:
                labels[group] = "many"
        return labels

    def _morale_hint(self, target: Army, confidence: str):
        if confidence not in ("good", "excellent"):
            return "unknown"
        if target.status == "RETREATING":
            return "shaken"
        if target.status in ("GARRISONED", "DOCKED"):
            return "steady"
        return "orderly"

    def _supply_hint(self, confidence: str, target_plan):
        if confidence not in ("good", "excellent"):
            return "unknown"
        if target_plan == "aggressive":
            return "moving hard"
        if target_plan == "cautious":
            return "conserved"
        return "adequate"

    def _target_status_modifier(self, target: Army):
        if target.status in ("MARCHING", "SAILING"):
            return -5
        if target.status in ("GARRISONED", "DOCKED"):
            return 5
        if target.status == "RETREATING":
            return -8
        return 0

    def _large_target_modifier(self, target: Army):
        if (target.troop_count or 0) >= 8000:
            return -12
        if (target.troop_count or 0) >= 3000:
            return -7
        return 0

    async def _score_scout(self, scout: Army, target: Army, terrain: str):
        scout_martial = await self._get_army_martial(scout)
        target_martial = await self._get_army_martial(target)
        distance = self._distance(
            scout.location_x, scout.location_y, target.location_x, target.location_y
        )
        scout_range = self._scout_range(scout, scout_martial)
        if distance > scout_range:
            return None, {
                "distance": int(distance),
                "range": int(scout_range),
            }

        target_plan = await self._target_plan(target)
        terrain_mods = SEA_TERRAIN_SCOUT_MODIFIERS if scout.army_type == "SEA" else LAND_TERRAIN_SCOUT_MODIFIERS
        score = (
            50
            + scout_martial * 2
            - target_martial
            + self._mobility_bonus(scout)
            + terrain_mods.get(terrain, 0)
            - int(distance / 20)
            - PLAN_CONCEALMENT.get((target_plan or "").lower(), 0)
            - self._target_status_modifier(target)
            - self._large_target_modifier(target)
            + random.randint(-20, 20)
        )
        return score, {
            "distance": int(distance),
            "range": int(scout_range),
            "target_plan": target_plan,
        }

    async def _store_report(
        self,
        game_id,
        scout,
        target,
        requester_house_id,
        report_type,
        confidence,
        result,
        target_fief_id=None,
    ):
        report = ScoutReport(
            game_id=game_id,
            scout_army_id=scout.army_id,
            target_army_id=target.army_id if target else None,
            target_fief_id=target_fief_id,
            requester_house_id=requester_house_id,
            target_house_id=target.house_id if target else None,
            report_type=report_type,
            confidence=confidence,
            result=result,
            expires_at=datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(days=7),
        )
        self.session.add(report)
        await self.session.flush()
        return report

    async def scout_army(self, game_id: int, discord_id: int, scout_army_id: int, target_army_id: int, is_gm=False):
        scout = await self.session.get(Army, scout_army_id, options=[selectinload(Army.house)])
        target = await self.session.get(Army, target_army_id, options=[selectinload(Army.house)])
        if not scout or not target:
            return False, "Scout or target army not found.", None, None
        if scout.game_id != game_id or target.game_id != game_id:
            return False, "Scout and target must belong to this game.", None, None

        requester_house_id = scout.house_id
        if not is_gm:
            requester_house_id = await self._get_player_house_id(game_id, discord_id)
            if requester_house_id != scout.house_id:
                return False, "You do not control that scouting force.", None, None

        terrain = await self._nearest_terrain(
            game_id, target.location_x, target.location_y, scout.army_type
        )
        score, details = await self._score_scout(scout, target, terrain)
        if score is None:
            return (
                False,
                f"Target is too far away to scout. Distance {details['distance']}, range {details['range']}.",
                None,
                None,
            )

        confidence = self._confidence_for_score(score)
        misleading = confidence == "failed" and random.random() < 0.45
        target_plan = details.get("target_plan")
        result = {
            "target_name": target.commander_name,
            "target_house": target.house.name if target.house else "Unknown",
            "terrain": terrain,
            "distance": details["distance"],
            "confidence_score": score,
            "estimated_size": self._size_band(target.troop_count or 0, confidence, misleading),
            "estimated_count": self._count_band(target.troop_count or 0, confidence, misleading),
            "composition": self._composition_summary(target, confidence, misleading),
            "status": target.status if confidence in ("basic", "good", "excellent") else "unclear",
            "morale_hint": self._morale_hint(target, confidence),
            "supply_hint": self._supply_hint(confidence, target_plan),
            "likely_plan": (
                target_plan
                if confidence == "excellent"
                else "possible " + target_plan
                if confidence == "good" and target_plan
                else "unknown"
            ),
            "warnings": [],
        }
        if misleading:
            result["warnings"].append("report may be compromised by poor visibility or enemy deception")
        if confidence in ("failed", "poor"):
            result["warnings"].append("enemy outriders may have spotted the scouts")

        report = await self._store_report(
            game_id, scout, target, requester_house_id, "army", confidence, result
        )
        await self.session.commit()
        alert = await self._target_alert(game_id, target.house_id, confidence)
        return True, "Scout report created.", report, alert

    async def scout_area(self, game_id: int, discord_id: int, scout_army_id: int, location_name: str, is_gm=False):
        scout = await self.session.get(Army, scout_army_id, options=[selectinload(Army.house)])
        if not scout or scout.game_id != game_id:
            return False, "Scout army not found.", None, None

        requester_house_id = scout.house_id
        if not is_gm:
            requester_house_id = await self._get_player_house_id(game_id, discord_id)
            if requester_house_id != scout.house_id:
                return False, "You do not control that scouting force.", None, None

        stmt_fief = select(Fief).where(Fief.game_id == game_id, Fief.name.ilike(location_name.strip('"')))
        fief = (await self.session.execute(stmt_fief)).scalars().first()
        if not fief:
            return False, "Location not found.", None, None

        scout_martial = await self._get_army_martial(scout)
        radius = min(450, self._scout_range(scout, scout_martial))
        dist_to_area = self._distance(scout.location_x, scout.location_y, fief.location_x, fief.location_y)
        if dist_to_area > radius:
            return (
                False,
                f"Area is too far away to scout. Distance {int(dist_to_area)}, range {int(radius)}.",
                None,
                None,
            )

        stmt_armies = (
            select(Army)
            .where(
                Army.game_id == game_id,
                Army.army_id != scout.army_id,
                Army.location_x >= fief.location_x - 180,
                Army.location_x <= fief.location_x + 180,
                Army.location_y >= fief.location_y - 180,
                Army.location_y <= fief.location_y + 180,
            )
            .options(selectinload(Army.house))
        )
        armies = (await self.session.execute(stmt_armies)).scalars().all()
        visible = []
        alerts = []
        for target in armies:
            terrain = await self._nearest_terrain(
                game_id, target.location_x, target.location_y, scout.army_type
            )
            score, details = await self._score_scout(scout, target, terrain)
            if score is None:
                continue
            confidence = self._confidence_for_score(score)
            if confidence == "failed":
                if alert := await self._target_alert(game_id, target.house_id, confidence):
                    alerts.append(alert)
                continue
            visible.append(
                {
                    "target_name": target.commander_name,
                    "target_house": target.house.name if target.house else "Unknown",
                    "estimated_size": self._size_band(target.troop_count or 0, confidence),
                    "estimated_count": self._count_band(target.troop_count or 0, confidence),
                    "confidence": confidence,
                    "status": target.status if confidence in ("basic", "good", "excellent") else "unclear",
                }
            )
            if confidence == "poor":
                if alert := await self._target_alert(game_id, target.house_id, confidence):
                    alerts.append(alert)

        result = {
            "location": fief.name,
            "region": fief.region,
            "searched_radius": 180,
            "forces": visible,
            "summary": f"{len(visible)} force(s) detected",
        }
        confidence = "basic" if visible else "poor"
        report = await self._store_report(
            game_id,
            scout,
            None,
            requester_house_id,
            "area",
            confidence,
            result,
            target_fief_id=fief.fief_id,
        )
        await self.session.commit()
        return True, "Area scout report created.", report, alerts[0] if alerts else None

    async def _target_alert(self, game_id: int, target_house_id: int, confidence: str):
        if confidence not in ("failed", "poor") or not target_house_id:
            return None
        stmt = (
            select(GamePlayer)
            .where(
                GamePlayer.game_id == game_id,
                GamePlayer.claimed_house_id == target_house_id,
                GamePlayer.private_channel_id.is_not(None),
            )
            .options(selectinload(GamePlayer.user))
        )
        player = (await self.session.execute(stmt)).scalars().first()
        if not player:
            return None
        return {
            "channel_id": player.private_channel_id,
            "discord_id": player.user.discord_id if player.user else None,
            "message": "Enemy scouts were seen near your lines.",
        }

    async def recent_reports(self, game_id: int, discord_id: int, limit: int = 5, is_gm=False):
        house_id = None if is_gm else await self._get_player_house_id(game_id, discord_id)
        stmt = (
            select(ScoutReport)
            .where(ScoutReport.game_id == game_id)
            .order_by(ScoutReport.created_at.desc())
            .limit(limit)
        )
        if house_id is not None:
            stmt = stmt.where(ScoutReport.requester_house_id == house_id)
        reports = (await self.session.execute(stmt)).scalars().all()
        return reports
