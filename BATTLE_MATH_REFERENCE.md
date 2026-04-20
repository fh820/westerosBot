# Battle Math Reference

This document lists the formulas currently used by the bot for field battles, sieges, and casualties. It is meant to be shared with troop data so another AI, GM, or tester can check whether a matchup produces sensible results.

Primary source code: `app/services/battle_service.py`.

## Key Inputs To Share For Testing

For each side, share:

- `army_type`: `LAND` or `SEA`
- `troop_count`
- `composition`, such as `{"infantry": 1000, "archers": 300, "cavalry": 120}`
- commander martial score, if known
- battle terrain
- attacker and defender plans
- ambush or defense setting, if the battle starts with one
- current morale and supply, if the battle has already started
- current score and phase, if mid-battle

For sieges, also share:

- wall integrity
- attacker and defender supply
- attacker and defender morale
- attacker action
- defender action
- whether a blockade fleet is attached

## Unit Battle Power

Each unit contributes weighted battle value:

| Unit | Value |
| --- | ---: |
| knights | 15.0 |
| cavalry | 5.0 |
| infantry | 3.5 |
| archers | 2.5 |
| militia | 1.0 |
| warships | 100.0 |
| ships | 100.0 |
| ship | 100.0 |

Formula:

```text
unit_value_total = sum(unit_count * unit_value)
battle_power = unit_value_total / 250
```

Example:

```text
1000 infantry, 300 archers, 100 cavalry
unit_value_total = (1000 * 3.5) + (300 * 2.5) + (100 * 5)
                 = 4750
battle_power = 4750 / 250 = 19
```

Important: if a unit name is not in the table, it contributes `0`.

## Starting Field Battle Odds

Field battles must be land-vs-land or sea-vs-sea. Mixed land/sea field battles are rejected.

Commander bonus:

```text
commander_bonus = martial / 3
```

Land-only attacker ambush bonuses:

| Ambush | Attacker Bonus |
| --- | ---: |
| extreme | +15 |
| good | +10 |
| decent | +5 |
| failed | -5 |
| none/unknown | +0 |

Land-only defender defense bonuses:

| Defense | Defender Bonus |
| --- | ---: |
| major | +20 |
| significant | +10 |
| minor | +5 |
| none/unknown | +0 |

Outnumbering bonus:

```text
if attacker_troop_count > defender_troop_count * 2:
    attacker_bonus += 4
elif defender_troop_count > attacker_troop_count * 2:
    defender_bonus += 4
```

Totals:

```text
attacker_total = attacker_battle_power + attacker_commander_bonus + manual_attacker_bonus + ambush_bonus + attacker_outnumber_bonus
defender_total = defender_battle_power + defender_commander_bonus + manual_defender_bonus + defense_bonus + defender_outnumber_bonus
```

Base odds:

```text
if attacker_total + defender_total == 0:
    base_odds = 50
else:
    base_odds = int((attacker_total / (attacker_total + defender_total)) * 100)

base_odds is clamped from 1 to 99
```

Momentum from existing score:

```text
momentum = (attacker_score - defender_score) * 5
starting_current_odds = clamp(base_odds + momentum, 1, 99)
```

`current_odds` means the attacker wins on a d100 roll from `1` through `current_odds`.

## Field Battle Phases

Current field battles use five phases:

```text
SKIRMISH -> MANEUVER -> CLASH -> PRESS -> ROUT -> COMPLETE
```

Each phase recalculates the attacker's phase odds from the battle's current odds:

```text
morale_adjustment = (attacker_morale - defender_morale) / 4
supply_adjustment = (attacker_supply - defender_supply) / 8
context_modifier = plan_modifier + terrain_modifier

phase_odds = int(clamp(
    current_odds + morale_adjustment + supply_adjustment + context_modifier,
    5,
    95
))
```

Roll:

```text
roll = random integer from 1 to 100
attacker_wins_phase = roll <= phase_odds
```

### Phase Results

| Phase | Winner Loss | Loser Loss | Winner Morale | Loser Morale | Current Odds Shift |
| --- | ---: | ---: | ---: | ---: | ---: |
| SKIRMISH | 1.5% | 3% | +2 | -6 | 5 |
| MANEUVER | 2% | 5% | +3 | -8 | 5 |
| CLASH | 4% | 8% | +5 | -12 | 8 |
| PRESS | 3% | 7% | +4 | -10 | 6 |
| ROUT | 2% | 10% | +3 | -16 | 0 |

For sea battles, the phase loss percentages are multiplied by `0.8`.

If the attacker wins the phase:

```text
attacker_score += 1
attacker_morale = clamp(attacker_morale + winner_morale, 0, 100)
defender_morale = clamp(defender_morale - loser_morale, 0, 100)
current_odds = clamp(current_odds + odds_shift, 5, 95)
```

If the defender wins the phase:

```text
defender_score += 1
defender_morale = clamp(defender_morale + winner_morale, 0, 100)
attacker_morale = clamp(attacker_morale - loser_morale, 0, 100)
current_odds = clamp(current_odds - odds_shift, 5, 95)
```

## Field Plans

Allowed plans:

```text
aggressive, defensive, flank, feint, cautious, ambush, reserve
```

Default plan for both sides:

```text
cautious
```

Plan matchup modifier is attacker-focused. Positive helps attacker odds; negative helps defender odds.

| Attacker Plan | Defender Plan | Modifier |
| --- | --- | ---: |
| aggressive | defensive | -10 |
| aggressive | feint | +6 |
| aggressive | cautious | +4 |
| defensive | aggressive | +8 |
| defensive | flank | -6 |
| flank | defensive | +8 |
| flank | cautious | -5 |
| feint | aggressive | +9 |
| feint | defensive | -4 |
| cautious | ambush | +10 |
| cautious | feint | -5 |
| ambush | aggressive | +12 |
| ambush | cautious | -10 |
| reserve | aggressive | +5 |
| reserve | flank | +4 |

Phase plan modifiers:

| Phase | Plan | Modifier |
| --- | --- | ---: |
| SKIRMISH | ambush | +8 |
| SKIRMISH | cautious | +3 |
| SKIRMISH | feint | +4 |
| SKIRMISH | aggressive | -2 |
| MANEUVER | feint | +5 |
| MANEUVER | flank | +4 |
| MANEUVER | cautious | +2 |
| MANEUVER | reserve | +2 |
| CLASH | aggressive | +6 |
| CLASH | defensive | +3 |
| CLASH | reserve | +4 |
| CLASH | flank | +5 |
| PRESS | aggressive | +5 |
| PRESS | flank | +5 |
| PRESS | reserve | +3 |
| PRESS | defensive | -2 |
| ROUT | flank | +6 |
| ROUT | aggressive | +4 |
| ROUT | cautious | -4 |
| ROUT | reserve | +3 |

Formula:

```text
plan_modifier = matchup_modifier(attacker_plan, defender_plan)
              + attacker_phase_plan_modifier
              - defender_phase_plan_modifier
```

## Plan Casualty Multipliers

After phase loss percentages are chosen, each side's losses are multiplied by that side's plan multiplier.

| Plan | Multiplier |
| --- | --- |
| defensive | `0.85` |
| aggressive | `0.95` if the side won, else `1.15` |
| cautious | `0.90` |
| ambush | `0.85` if the side won during SKIRMISH, else `1.10` |
| flank | `0.90` if the side won during ROUT, else `1.05` |
| reserve | `0.90` during CLASH, PRESS, or ROUT, else `1.00` |
| feint | `1.00` |

Phase casualty formula:

```text
losses = int(current_troop_count * phase_loss_pct * plan_loss_multiplier)
if losses == 0 and current_troop_count > 0:
    losses = 1
losses = min(losses, current_troop_count)
```

## Terrain Modifiers

Terrain modifier is attacker terrain score minus defender terrain score:

```text
terrain_modifier = attacker_terrain_score - defender_terrain_score
```

Unit ratios:

```text
cavalry_ratio = (knights + cavalry) / troop_count
archer_ratio = archers / troop_count
militia_ratio = militia / troop_count
infantry_ratio = infantry / troop_count
```

Land terrain scores:

| Terrain | Formula |
| --- | --- |
| plains | `+int(cavalry_ratio * 18)` during CLASH or ROUT |
| hills | SKIRMISH: `+int(archer_ratio * 12)`; CLASH: `+int(infantry_ratio * 4) - int(cavalry_ratio * 5)` |
| forest | `+int((archer_ratio + militia_ratio) * 8) - int(cavalry_ratio * 14)` |
| mountains | `+int((archer_ratio + infantry_ratio) * 6) - int(cavalry_ratio * 16)` |
| river | attacker during CLASH: `-14`; defender always: `+10`; all sides: `+int(archer_ratio * 5)` |
| marsh | `+int(militia_ratio * 8) - int(cavalry_ratio * 18)`; additional `-4` during ROUT |
| urban | `+int((infantry_ratio + militia_ratio) * 8) - int(cavalry_ratio * 12)`; additional `-5` during ROUT |
| coast | `+int(infantry_ratio * 3)` |
| unknown | `0` |

Sea terrain modifier is a direct attacker-focused modifier:

| Terrain | Modifier |
| --- | ---: |
| open_sea | +2 |
| coast | +1, or -2 during ROUT |
| strait | -4 during SKIRMISH, otherwise +3 |
| storm | -8 |
| unknown | 0 |

## Field Battle Winner

A field battle can end early from morale:

```text
if attacker_morale <= 25 and defender_morale > attacker_morale:
    defender wins
if defender_morale <= 25 and attacker_morale > defender_morale:
    attacker wins
```

If all phases complete:

```text
if attacker_score > defender_score:
    attacker wins
elif defender_score > attacker_score:
    defender wins
else:
    side with higher morale wins
```

Tied score and tied morale favors the attacker.

## Field Battle Aftermath Casualties

After a battle is resolved, rout casualties are applied based on the loser's score:

```text
score_index = clamp(5 - loser_score, 0, 5)
```

A `5-0` result gives index `5`; a `5-4` result gives index `1`.

Land winner rout losses:

| Index | Winner Loss |
| ---: | ---: |
| 0 | 25% |
| 1 | 20% |
| 2 | 15% |
| 3 | 10% |
| 4 | 7% |
| 5 | 5% |

Land loser rout losses:

| Index | Loser Loss |
| ---: | ---: |
| 0 | 40% |
| 1 | 50% |
| 2 | 60% |
| 3 | 70% |
| 4 | 80% |
| 5 | 90% |

Sea winner rout losses:

| Index | Winner Loss |
| ---: | ---: |
| 0 | 20% |
| 1 | 16% |
| 2 | 12% |
| 3 | 8% |
| 4 | 5% |
| 5 | 4% |

Sea loser rout losses:

| Index | Loser Loss |
| ---: | ---: |
| 0 | 32% |
| 1 | 40% |
| 2 | 48% |
| 3 | 56% |
| 4 | 64% |
| 5 | 72% |

Aftermath casualty formula:

```text
rout_losses = int(current_troop_count * rout_loss_pct)
if side_is_loser and rout_losses == 0 and current_troop_count > 0:
    rout_losses = 1
rout_losses = min(rout_losses, current_troop_count)
```

Retreat check for a non-siege loser with survivors:

```text
retreat_odds = 40 + (loser_martial * 2)
retreat succeeds if d100 <= retreat_odds
```

If retreat fails, the losing army is destroyed. If retreat succeeds, it becomes `RETREATING`.

## Sea Cargo Casualties

When a fleet loses ships, carried cargo troops are reduced by the same survival rate as ships:

```text
survival_rate = (initial_ships - ship_losses) / initial_ships
new_cargo_troop_count = int(old_cargo_troop_count * survival_rate)
```

Cargo composition is split/scaled to match the new cargo count.

## Composition Scaling

Whenever troop count is reduced, composition is scaled proportionally:

```text
ratio = new_total_count / current_composition_total
new_unit_count = int(old_unit_count * ratio)
```

Rounding remainder is added to the largest remaining unit group.

If the current composition total is `0`, or the new total is `0`, composition becomes empty.

## Starting Siege Odds

Sieges require a land attacker and a land garrison at the fief.

Starting siege odds:

```text
attacker_score_value = attacker_battle_power + (attacker_martial / 3)
defender_score_value = defender_battle_power + (defender_martial / 3) + defense_bonus

odds = 50 + (attacker_score_value - defender_score_value)
current_odds = int(clamp(odds, 10, 90))
```

Siege defense bonus:

| Defense | Bonus |
| --- | ---: |
| major | +20 |
| significant | +10 |
| minor | +5 |
| siege_camp | +3 |
| none/unknown | +0 |

Starting siege state:

```text
phase = WALLS
siege_phase = WALLS
wall_integrity = 100
attacker_morale = 100
defender_morale = 100
attacker_supply = 100
defender_supply = 100
attacker_action = invest
defender_action = ration
```

## Siege Actions

Attacker actions:

| Action | Wall | Defender Supply | Attacker Supply | Defender Morale | Attacker Morale |
| --- | ---: | ---: | ---: | ---: | ---: |
| invest | 0 | -10 | -3 | -4 | -1 |
| bombard | -12 | -5 | -8 | -7 | -2 |
| mine | -16 | -3 | -10 | -6 | -3 |
| assault | -8 | -2 | -6 | -12 | -8 |
| raid | 0 | -8 | +6 | -5 | +2 |

Defender actions:

| Action | Wall | Defender Supply | Attacker Supply | Defender Morale | Attacker Morale |
| --- | ---: | ---: | ---: | ---: | ---: |
| repair | +10 | -4 | 0 | +2 | 0 |
| sally | 0 | -8 | -10 | -4 | -7 |
| ration | 0 | +5 | 0 | -4 | 0 |
| counter_mine | +8 | -5 | 0 | +1 | 0 |
| ambush | 0 | -4 | 0 | +2 | -8 |

Special action interactions:

```text
if attacker_action == mine and defender_action == counter_mine:
    attacker_wall_effect = int(attacker_wall_effect / 3)
    defender_morale_effect += 2

if attacker_action == bombard and defender_action == repair:
    defender_wall_effect = int(defender_wall_effect / 2)

if attacker_action == assault and defender_action == ambush:
    attacker_morale_effect -= 6
```

Blockade effect each siege turn:

```text
if blockade_fleet_id exists:
    defender_supply_effect -= 8
    defender_morale_effect -= 2
```

Attaching a blockade also immediately applies:

```text
defender_supply = clamp(defender_supply - 5, 0, 100)
```

To attach a blockade, the fleet must be within `150` distance of the fief, belong to the besieging house, and hostile nearby fleets must not have enough ships to contest it:

```text
distance = sqrt((fleet_x - fief_x)^2 + (fleet_y - fief_y)^2)
hostile_screen = sum(hostile_fleet_ships within 150 distance)

blockade fails if hostile_screen >= max(1, int(blockading_fleet_ships * 0.75))
```

## Siege Turn Resolution

Each walls-phase siege turn:

```text
wall_delta = attacker_wall_effect + defender_wall_effect + random_integer(-3, 3)
attacker_supply_delta = attacker_supply_effect + defender_attacker_supply_effect
defender_supply_delta = attacker_defender_supply_effect + defender_supply_effect
attacker_morale_delta = attacker_morale_effect + defender_attacker_morale_effect
defender_morale_delta = attacker_defender_morale_effect + defender_morale_effect

wall_integrity = int(clamp(wall_integrity + wall_delta, 0, 120))
attacker_supply = int(clamp(attacker_supply + attacker_supply_delta, 0, 100))
defender_supply = int(clamp(defender_supply + defender_supply_delta, 0, 100))
attacker_morale = int(clamp(attacker_morale + attacker_morale_delta, 0, 100))
defender_morale = int(clamp(defender_morale + defender_morale_delta, 0, 100))
round_number += 1
```

Walls-phase siege casualties:

```text
if attacker_action == assault:
    attacker_loss_pct = 0.06 if wall_integrity <= 40 else 0.12
    if defender_action == ambush:
        attacker_loss_pct += 0.05
    defender_loss_pct = 0.08 if wall_integrity <= 40 else 0.04

elif defender_action == sally:
    attacker_loss_pct = 0.04
    defender_loss_pct = 0.03

elif attacker_action in (bombard, mine):
    defender_loss_pct = 0.01

else:
    no direct troop losses
```

These percentages use the same phase-loss helper:

```text
losses = int(current_troop_count * loss_pct)
minimum 1 loss if loss_pct > 0 and current_troop_count > 0
```

Walls-phase siege ending checks:

```text
if defender_supply <= 0 or defender_morale <= 0:
    attacker wins

elif attacker_supply <= 0 or attacker_morale <= 0:
    defender wins

elif wall_integrity <= 0:
    phase = STREETS
    siege_phase = STREETS
    current_odds = int(clamp(current_odds + 10, 10, 90))
```

After each walls-phase turn, attacker and defender actions reset to defaults:

```text
attacker_action = invest
defender_action = ration
```

## Siege Street Fighting

Once walls fall, the next siege turn resolves street fighting.

Street odds:

```text
odds = int(clamp(
    current_odds
    + ((attacker_morale - defender_morale) / 3)
    + ((attacker_supply - defender_supply) / 6),
    10,
    90
))

attacker_wins = d100 <= odds
```

If attacker wins:

```text
attacker loses 10%
defender loses 18%
attacker_score = 5
defender_score = 0
phase = COMPLETE
winner = Attacker
```

If defender wins:

```text
attacker loses 16%
defender loses 8%
attacker_score = 0
defender_score = 5
phase = COMPLETE
winner = Defender
```

## Siege Consequences

Winning the siege fight does not automatically transfer ownership. The GM must run the siege consequence command after the siege is complete.

If attacker won, the fief changes owner and integration is reset:

```text
fief.owner_id = attacker.house_id
fief.integration = 0.10
```

If defender won, the siege is repelled and the battle is deleted.

## Manual Casualty Command

The GM command `!gm_war calc_casualties <winner_id> <loser_id> <score> <retreat>` currently calls `BattleService.manual_casualty_calculation`.

Score parsing:

```text
scores = integers from score string
loser_score = min(scores)
severity_index = clamp(5 - loser_score, 0, 5)
```

The same land or sea casualty tables from "Field Battle Aftermath Casualties" are used.

Manual casualty formula:

```text
loss = int(troop_count * casualty_pct)
if loss == 0 and troop_count > 0:
    loss = 1
troop_count = max(0, troop_count - loss)
```

If `retreat` is false, the loser army is deleted after casualties. If `retreat` is true, the loser becomes `RETREATING`.

## Common Wonky-Result Checks

Use these checks when asking another AI to review a proposed battle:

- Unknown unit names have `0` BP, so typos in composition can make elite forces useless.
- Ships are worth `100` each before dividing by `250`, so even modest fleets get large BP.
- Commander martial is divided by `3`; very high martial scores can swing odds hard.
- Battle power is based on composition, but outnumbering is based on raw `troop_count`.
- Starting field odds clamp to `1-99`, but phase odds clamp to `5-95`, so nothing is guaranteed during phase rolls.
- Morale differences are strong: every `4` morale gap gives `1` odds point in field phases.
- Supply differences are milder: every `8` supply gap gives `1` odds point in field phases.
- Terrain bonuses are integer-truncated ratios, so small or mixed armies may see terrain effects round down to `0`.
- Field battles apply phase casualties first, then aftermath rout casualties later.
- A crushing field result can be very bloody because rout losses are applied to already-reduced survivors.
- Siege turns can end by supply or morale collapse without walls reaching `0`.
- Assault casualty percentages are based on wall integrity after the turn's wall changes have already been applied.
- Siege actions reset after every walls-phase turn, so forgotten orders become attacker `invest` and defender `ration`.
- Sea cargo losses follow ship survival rate, so cargo can vanish quickly if ships are destroyed.
