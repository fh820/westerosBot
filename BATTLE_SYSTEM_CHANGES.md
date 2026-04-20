# Battle System Changes

This guide explains how battles, sieges, naval fighting, blockades, and scouting work from a player point of view.

The system is meant to feel like command under uncertainty. You will see enough to make smart decisions, but not every hidden modifier. GMs can see deeper audit details for fairness and troubleshooting.

## The Big Idea

The old simple battle flow has been replaced by a phased war system.

The new system cares about:

- Army or fleet size.
- Unit mix.
- Commander quality.
- Terrain.
- Tactical plans.
- Morale.
- Supplies.
- Walls during sieges.
- Blockades during coastal sieges.
- Scouting and imperfect intelligence.
- A limited amount of luck.

Numbers still matter, but they are not everything. A better plan, stronger terrain, higher morale, or good scouting can change a fight.

## What Players Need To Do

Before a fight:

```text
!army
!scout <your_army_id> <target_army_id>
!scout_area <your_army_id> <location_name>
!intel
```

When a field battle starts:

```text
!battle_plan <battle_id> <attacker|defender> <plan>
```

When a siege starts:

```text
!siege_action <battle_id> <attacker|defender> <action>
```

If a coastal siege needs naval support:

```text
!sail <fleet_id>
```

Then tell the GM which fleet should support the siege as a blockade.

## Public Rolls And Hidden Math

Public battle reports can show the roll and result.

Example:

```text
Roll: 63
Result: Defender won the phase.
```

The public report does not show every hidden modifier or target number. Those details go to GMs.

When you see odds like this:

```text
Attacker: 1-41
Defender: 42-100
```

That means the bot is using a d100-style range.

- A roll from `1` to `41` favors the attacker.
- A roll from `42` to `100` favors the defender.
- This is roughly a 41 percent attacker chance and 59 percent defender chance.

For field battles, those odds are for the current phase.

For sieges, the panel may show a `Street Fighting Outlook`. That number is mainly used if the walls are breached. Normal siege turns are not just one odds roll; they are action-effect turns.

## Field Battles

Field battles are land or sea battles between two armies or two fleets.

They resolve through phases:

- `Skirmish`: opening shots, probes, scouts, light troops, and first contact.
- `Main Clash`: the main body commits and the hardest fighting happens.
- `Rout or Pursuit`: one side breaks, withdraws, or tries to run down the enemy.

The GM presses `Resolve Phase` to move the battle forward.

Each phase can affect:

- Casualties.
- Morale.
- Momentum.
- The next phase.
- The final outcome.

Winning the first phase helps, but it does not guarantee victory. Losing early hurts, but it does not always doom you.

## Battle Plans

Use this command when your side is in a field battle:

```text
!battle_plan <battle_id> <attacker|defender> <plan>
```

Alias:

```text
!battle-plan <battle_id> <attacker|defender> <plan>
```

Example:

```text
!battle_plan 12 defender defensive
```

When a valid plan is submitted, the battle panel updates.

### Battle Plan Options

`aggressive`
: Push hard, force contact, and try to seize momentum. Useful when you want a decisive clash, but it can be costly if the enemy is ready.

`defensive`
: Hold ground, absorb pressure, and punish reckless attacks. Strong when terrain favors you or the enemy is likely to charge.

`flank`
: Try to stretch, turn, or roll up the enemy line. Strong with mobility and room to maneuver.

`feint`
: Bait the enemy into reacting badly. Useful when you expect aggression or want to disrupt their plan.

`cautious`
: Avoid traps, preserve strength, and limit exposure. Useful when you fear ambushes or bad information.

`ambush`
: Seek surprise and disorder. Strong when concealment, scouting, or terrain supports it.

`reserve`
: Hold troops back for the decisive moment. Useful when you expect the fight to swing later.

## Terrain

The GM sets terrain when starting or managing a battle.

Terrain options:

- `unknown`
- `plains`
- `hills`
- `forest`
- `mountains`
- `river`
- `marsh`
- `urban`
- `coast`
- `open_sea`
- `strait`
- `storm`

Terrain examples:

- `plains`: favors open maneuver and mounted pressure.
- `hills`: rewards strong positions and missile troops.
- `forest`: complicates cavalry and favors concealment.
- `mountains`: favors hard defensive ground.
- `river`: punishes bad crossings.
- `marsh`: slows movement and makes pursuit messy.
- `urban`: makes fighting close, chaotic, and costly.
- `coast`: can matter for landings and coastal fighting.
- `open_sea`: normal naval fighting.
- `strait`: cramped naval fighting.
- `storm`: dangerous naval conditions.

Players should mention terrain in their roleplay and plans. If you are defending a river, choose like someone defending a river. If you are charging cavalry through a forest, expect trouble.

## Sea Battles

Sea battles use the same broad phased system as field battles.

Simple naval mode is active:

- All ships are treated as ships.
- There are no separate ship classes.
- Fleet size, commander quality, terrain, morale, supply, and plan matter.

Sea battle terrain usually uses:

- `open_sea`
- `coast`
- `strait`
- `storm`

Fleets can:

- Fight other fleets.
- Carry land armies.
- Contest coastal waters.
- Support coastal sieges through blockades.

## Sieges

Sieges are different from field battles. They are not won by scoring normal rounds. They are turn-by-turn pressure contests.

A siege tracks:

- Wall condition.
- Defender supplies.
- Attacker supplies.
- Defender morale.
- Attacker morale.
- Current attacker action.
- Current defender action.
- Blockade support, if present.

The siege loop is:

```text
Attacker chooses action.
Defender chooses action.
GM presses Resolve Phase.
Panel updates.
Repeat.
```

The attacker is trying to break walls, supplies, or morale.

The defender is trying to preserve walls, supplies, and morale until the attacker fails, negotiates, withdraws, or gets hit by a relief force.

## Siege Actions

Use this command when your side is in a siege:

```text
!siege_action <battle_id> <attacker|defender> <action>
```

Example:

```text
!siege_action 73 attacker bombard
```

When a valid action is submitted, the siege panel updates.

### Attacker Siege Actions

`invest`
: Surround the fief and starve it. Slow, steady, and usually safer than storming.

`bombard`
: Use siege engines and pressure to damage walls and morale. Often answered by `repair`.

`mine`
: Tunnel, sap, or undermine the defenses. Often answered by `counter_mine`.

`assault`
: Storm the defenses. Fast and dangerous. Very costly against strong walls or prepared defenders.

`raid`
: Attack stores, foragers, weak points, and supply routes. Useful for supply pressure and disruption.

### Defender Siege Actions

`repair`
: Keep the walls standing. Strong against bombardment.

`sally`
: Strike out at the besiegers. Risky, but can damage attacker morale and supply.

`ration`
: Stretch supplies. Helps keep food going, but morale suffers.

`counter_mine`
: Hunt tunnels, reinforce weak points, and blunt mining attempts.

`ambush`
: Prepare traps and local counterblows. Especially dangerous if the attacker assaults.

## How Sieges End

A siege can end in several ways:

- Walls collapse and the fight moves to street fighting.
- Defender supplies collapse.
- Defender morale collapses.
- Attacker supplies collapse.
- Attacker morale collapses.
- A relief army or fleet changes the situation.
- The sides negotiate surrender or withdrawal.
- The GM rules an outcome based on roleplay and campaign events.

If the attacker wins the siege, the GM applies the conquest result with:

```text
!resolve_siege <battle_id>
```

Winning the siege and transferring the fief are separate steps.

## Street Fighting

If the walls break, the siege can move into street fighting.

Street fighting is brutal. The attacker has a way in, but the defender may still make the final taking of the fief costly.

Public street fighting reports show the roll and result:

```text
Roll: 44
Result: Attacker won the breach fight.
```

The detailed target and modifiers remain GM-facing.

## Blockades

Blockades are naval support for coastal sieges.

Players move fleets into position:

```text
!sail <fleet_id>
```

Then coordinate with the GM. The GM attaches the blockade:

```text
!blockade <fleet_id> <battle_id>
```

A blockade can:

- Increase supply pressure on defenders.
- Make coastal fiefs harder to sustain.
- Make long sieges more dangerous.
- Force enemies to contest the sea.

Blockades can be contested by hostile fleets. A blockade is not invisible magic; it is a military commitment.

## Scouting

Scouting gives imperfect intelligence. Reports can be excellent, useful, vague, misleading, or failed.

Scout a known army or fleet:

```text
!scout <your_army_id> <target_army_id>
```

Scout an area:

```text
!scout_area <your_army_id> <location_name>
```

Alias:

```text
!scout-area <your_army_id> <location_name>
```

Review recent reports:

```text
!intel
!intel 10
```

Scouting can reveal:

- Rough size.
- Rough composition.
- Status.
- Terrain.
- Morale hints.
- Supply hints.
- Possible plans.
- Warnings.

Scouting can be affected by:

- Distance.
- Terrain.
- Mobility.
- Commander quality.
- Target size.
- Target behavior.
- Chance.

Bad scouting may alert the target that scouts were spotted.

## Trial Flow: Field Battle

This is what a normal player-facing field battle might look like.

### Setup

The GM starts the battle:

```text
!battle 123 456 terrain=river
```

The battle panel appears in `#battle-reports`.

It may show:

```text
Battle Command (ID: 12)
Attacker: Northern Vanguard
Defender: River Guard
Phase: SKIRMISH
Terrain: river
Morale: Attacker 100 / Defender 100
Supply: Attacker 100 / Defender 100
Plans: Attacker cautious / Defender cautious
Battle Odds (Current Phase)
Attacker: 1-41
Defender: 42-100
```

### Player Choices

The attacker chooses:

```text
!battle_plan 12 attacker feint
```

The defender chooses:

```text
!battle_plan 12 defender defensive
```

The panel updates to:

```text
Plans: Attacker feint / Defender defensive
```

### GM Resolves Phase

The GM presses `Resolve Phase`.

Public report:

```text
Phase: Skirmish
Roll: 63
Result: Defender won the phase.
Plans, morale, supply, and terrain all shaped the result.

The River Guard held the river line and forced the attackers to commit badly.
```

Then the panel updates. Morale, casualties, current odds, and phase may change.

### Next Phase

Players may change plans before the next phase:

```text
!battle_plan 12 attacker aggressive
!battle_plan 12 defender reserve
```

The GM presses `Resolve Phase` again.

This repeats until the battle ends.

## Trial Flow: Siege

This is what a normal siege might look like.

### Setup

The GM starts the siege:

```text
!siege 9706 Dragonstone
```

The siege panel appears:

```text
Siege Command: Dragonstone (ID: 73)
Attacker: Army2
Defender: Garrison of Dragonstone
Phase: WALLS
Turn: 0
Terrain: fortification
Morale: Attacker 100 / Defender 100
Supply: Attacker 100 / Defender 100
Walls: 100
Actions: Attacker invest / Defender ration
Street Fighting Outlook
Attacker: 1-41
Defender: 42-100
```

The `Street Fighting Outlook` matters if the walls are breached. It is not the roll for every siege turn.

### Turn 1 Choices

The attacker chooses bombardment:

```text
!siege_action 73 attacker bombard
```

The defender chooses repairs:

```text
!siege_action 73 defender repair
```

The panel updates:

```text
Actions: Attacker bombard / Defender repair
```

The GM presses `Resolve Phase`.

Public report:

```text
Siege Turn 1
Walls: 96
Supplies: Attacker 92, Defender 91
Morale: Attacker 98, Defender 95

The attackers chose bombard while the defenders chose repair.
```

No public d100 roll appears here because this is a pressure turn, not a single contested phase roll.

### Turn 2 Choices

The attacker wants to rush:

```text
!siege_action 73 attacker assault
```

The defender expects the assault:

```text
!siege_action 73 defender ambush
```

The GM presses `Resolve Phase`.

Public report might show:

```text
Siege Turn 2
Walls: 88
Supplies: Attacker 86, Defender 87
Morale: Attacker 78, Defender 82

The attackers chose assault while the defenders chose ambush.
```

The attacker may take heavy losses. Assaulting strong walls is dangerous.

### Turn 3 Choices

The attacker changes plan:

```text
!siege_action 73 attacker invest
```

The defender rations:

```text
!siege_action 73 defender ration
```

The GM resolves another turn.

Over time, defender supplies and morale may collapse. Or the attacker may run low on supply and morale. Or walls may fall.

### If Walls Break

If walls reach collapse, the siege can enter street fighting.

The next resolution may show:

```text
Streets
Roll: 44
Result: Attacker won the breach fight.
```

If the attacker wins, the GM can then apply consequences:

```text
!resolve_siege 73
```

## Trial Flow: Coastal Siege With Blockade

The attacker has a fleet near Dragonstone.

Player moves the fleet:

```text
!sail 555
```

The player tells the GM:

```text
Fleet 555 is supporting Siege 73 as a blockade.
```

The GM attaches it:

```text
!blockade 555 73
```

The panel updates:

```text
Blockade Fleet: 555
```

Future siege turns put more pressure on defender supplies.

## What Players Should Remember

- Scout before committing to a major fight.
- Choose battle plans that match your army and terrain.
- Do not assume a bigger army always wins.
- Field battles are phase-based.
- Sieges are turn-based pressure contests.
- Siege actions update the panel before the GM resolves.
- Normal siege turns usually do not show d100 rolls.
- Field battle phases and street fighting show public rolls.
- Exact modifiers stay with the GM.
- Coastal sieges become much harsher when blockaded.
