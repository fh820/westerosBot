# WesterosBot Admin And GM Guide

This guide is for admins and GMs who need to run WesterosBot for a campaign. It focuses on setup, claims, moderation, GM overrides, battle flow, economy management, and recovery commands.

All commands use the `!` prefix.

## GM Roles And Permissions

There are two kinds of elevated access in the code:

- Discord administrator permissions.
- Database GM flag on `User.is_gm`.

Some commands require Discord administrator permission. Some GM command groups check the database GM flag. Server administrators often bypass player-only channel checks.

To set the database GM flag:

```text
!set_gm @Member true
!set_gm @Member false
```

## First-Time Game Setup

### Start A Game

```text
!setup_game [ruling_house] [mode]
```

Examples:

```text
!setup_game Targaryen SPLIT
!setup_game Baratheon UNIFIED
```

Defaults:

- `ruling_house`: `Targaryen`
- `mode`: `SPLIT`

Valid modes:

- `SPLIT`
- `UNIFIED`

What this does:

- Checks that no active game already exists.
- Loads `master_world_data.json`.
- Optionally applies an attached JSON patch.
- Creates the active game.
- Creates houses, fiefs, characters, armies, treasuries, manpower, and feudal relationships.
- Creates core server channels.
- Creates core roles.

### Setup With A Patch File

Attach a `.json` patch to the `!setup_game` message.

```text
!setup_game Baratheon SPLIT
```

The patch can override fields for castles/fiefs in master world data.

### Channels Created

Game logistics:

- `claims`
- `gm-alerts`
- `gm-requests`

In-character/public:

- `news-and-events`
- `battle-rumours`
- `battle-reports`
- `rumours`
- `gambling-den`
- `marriages`
- `declarations`
- `general-movements`
- `army-movements`
- `royal-decrees`
- `ravens-n-scrolls`
- `small-council`
- `westeros-ic`
- `kingslanding`
- `open-to-do-list`
- `tournament`

Player quarters are created later during claim approval.

## World Chronicle

The world chronicle is a lightweight GM timeline. It records important campaign events in the database so a GM can quickly see what happened, in what order, without digging through Discord history.

View recent events:

```text
!gm_chronicle
```

View a specific number of recent events:

```text
!gm_chronicle 50
```

Filter by category or search text:

```text
!gm_chronicle battle
!gm_chronicle siege
!gm_chronicle fief
!gm_chronicle 30 Riverrun
!gm_chronicle 30 "Storm's End"
```

Aliases:

```text
!chronicle
!timeline
```

Add a manual GM note:

```text
!gm_log_note The river crossing at Ruby Ford was closed by GM ruling.
```

The chronicle is GM-facing and concise. It is not the same as `#gm-alerts`: GM alerts contain live operational detail and math audits, while the chronicle records timeline facts such as battle starts, phase results, battle cancellations, siege turns, siege conclusions, and fief captures.

## Gambling

Setup creates `#gambling-den` as a public in-character channel for casual gambling.

Blackjack command:

```text
!gamble
!blackjack <bet>
!bj <bet>
```

The wager comes from the player's house treasury. The command uses button controls for `Hit`, `Stand`, and `Double`.

Payouts:

- Normal win: stake returned plus equal winnings.
- Blackjack: stake returned plus 1.5x winnings.
- Push: stake returned.
- Loss: wager is kept.
- Double: charges a second copy of the original bet, draws one card, then stands.

The current maximum bet is 10,000 gold.

### Roles Created

- `IronThrone`
- `SmallCouncil`
- `Hand of the King`
- `Master of Coin`
- `Master of Whisperers`
- `Master of Ships`
- `Master of Laws`
- `Lord Commander`
- `Grand Maester`

House roles are created as claims are approved or assigned.

## Ending Or Resetting A Game

### End Active Game Data

```text
!end_game CONFIRM
```

This deletes active game data from the database in a safe order.

### End And Purge Discord Assets

```text
!end_game CONFIRM PURGE
```

This also tries to delete game categories/channels and game roles.

Use with care.

## Claim Management

### Player Claim Flow

Players run:

```text
!claim <House or Character>
```

The bot sends the approval request to `#gm-requests`.

### Approve A Claim

```text
!approve @Member <claim_string>
```

Examples:

```text
!approve @Arya Stark
!approve @Player Sansa Stark
```

Approval does the following:

- Claims the house or character.
- Creates/assigns house role.
- Creates a private quarters channel.
- Locks the quarters channel ID to the player record.

### Deny A Claim

```text
!deny @Member [reason]
```

Example:

```text
!deny @Player This character is already reserved.
```

The bot attempts to DM the player.

### Clear A Stuck Pending Claim

```text
!reset_claim_lock @Member
```

Use this if a player submitted a claim and the ticket was deleted or handled outside the bot.

### Reset A Player's Quarters Lock

```text
!reset_quarters @Member
```

Use this when:

- Their private channel was deleted.
- Their saved channel ID is wrong.
- They cannot use `!me` in their quarters.

### Vacate A Claim

```text
!vacate @Member
```

This:

- Deletes their quarters if found.
- Removes related roles.
- Removes their `GamePlayer` record.

## Manual Player Assignment

### Assign House Head

```text
!set_head @Member <house_name>
```

Use this to manually assign a player as primary controller/head of a house.

### Set Crown

```text
!set_crown @Member
```

This:

- Moves the player into the royal house tied to King's Landing.
- Preserves their private channel link.
- Adjusts the house they left to be a vassal of the royal house.
- Assigns the relevant house role.

### Set Heir

```text
!set_heir @Member
```

Assigns the player to the Dragonstone/heir house.

### Force Grant A Fief

```text
!force_grant @Member <castle_name>
```

Example:

```text
!force_grant @Player Harrenhal
```

This:

- Transfers the fief to the target player's house.
- Transfers armies at that location.
- Transfers vassals from the old owner to the new owner.
- Transfers old owner treasury to the new owner.
- Recalculates manpower.
- Notifies the recipient's quarters.

## Royal And Political Admin

### Coronate A Player

```text
!coronate @Member
```

This:

- Assigns `IronThrone`.
- Removes `IronThrone` from others.
- Posts to `#royal-decrees`.
- Notifies the player's quarters.

### Appoint Small Council

```text
!appoint @Member <title>
```

Valid titles:

- Hand of the King
- Master of Coin
- Master of Whisperers
- Master of Ships
- Master of Laws
- Lord Commander
- Grand Maester

### Title Grants By Players

Players can run:

```text
!grant_title <Fief Name> <@User or Character Name>
```

Admins should know this transfers the fief, local garrison, vassal relation, and some upkeep gold if available.

## Scenario Management

```text
!load_scenario <scenario_name>
```

Examples:

```text
!load_scenario roberts_rebellion
!load_scenario dance_of_dragons
```

Scenario files live in:

- `app/scenarios/roberts_rebellion.json`
- `app/scenarios/dance_of_dragons.json`

## Game Rule Toggles

### Toggle Upkeep

```text
!toggleupkeep
```

Turns daily army upkeep/attrition on or off.

### Toggle Manpower And Recruitment

```text
!togglemanpower
```

Turns the recruitment/manpower system on or off for regular players. GMs may still bypass some restrictions.

### World Movement Rules

```text
!worldrule setbridge <bridge_name> <open|closed>
!worldrule setrivers <status>
!worldrule setsea <status>
```

Examples:

```text
!worldrule setbridge twins closed
!worldrule setrivers impassable
!worldrule setsea off
```

These change map/pathfinding behavior for crossings, rivers, and sea travel.

## Player Diagnostics

### Check Your Own DB State

```text
!debug_me
```

Shows how the database sees the command author.

### Player-Side Channel Diagnostic

Players or admins can use:

```text
!debug_access
```

This compares the current channel ID with the saved private channel ID.

### Terrain Debug

```text
!debug_terrain <x> <y>
```

Example:

```text
!debug_terrain 1024 768
```

Returns the terrain type at map coordinates.

## GM Dashboards And Lookup

### View Any House Dashboard

```text
!gm_info <house_name>
!gm_dashboard <house_name>
```

Example:

```text
!gm_info Stark
```

Shows the same style of detailed report players get, but for any house.

### Scan Location For Armies

```text
!gm_war scan_location <location_name or coords>
```

Examples:

```text
!gm_war scan_location Riverrun
!gm_war scan_location 1234,5678
```

Shows all armies/garrisons at a location.

### Find Fief IDs And Owners

```text
!gm_econ fief <fief_name>
```

Example:

```text
!gm_econ fief Winter
```

## GM Diplomacy

The GM diplomacy group is:

```text
!gm_diplomacy
```

### Make An NPC/GM House Call Land Banners

```text
!gm_diplomacy call_banners <target_house_id> <rally_point>
```

Example:

```text
!gm_diplomacy call_banners 101 Riverrun
```

### Make An NPC/GM House Call Naval Levies

```text
!gm_diplomacy call_levies_sea <target_house_id> <rally_point>
```

### Force Fealty

```text
!gm_diplomacy declare_fealty <vassal_house_id> <new_liege_name>
!gm_diplomacy set_liege <vassal_house_id> <new_liege_name>
```

Examples:

```text
!gm_diplomacy declare_fealty 201 Stark
!gm_diplomacy set_liege 201 Stark
```

### GM War Declaration

```text
!gm_diplomacy declare_war <aggressor_house_id> <target_house_name> [reason]
```

Example:

```text
!gm_diplomacy declare_war 101 Lannister Border raids
```

### Cancel A Pending Banner Or Levy Call

```text
!gm_diplomacy cancel_call <pending_call_id>
```

Use this if a banner panel is stuck or duplicated.

### Reassign All Vassals

```text
!gm_diplomacy mass_reassign <old_liege_id> <new_liege_id>
```

Example:

```text
!gm_diplomacy mass_reassign 704 600
```

### Manage Gate Access For NPC Houses

```text
!gm_gate_access <host_house_id> <add|remove> <target_house_id>
```

Example:

```text
!gm_gate_access 10 add 20
```

## Banner Call GM Panels

When players or GMs call banners and NPC vassals are involved, the bot creates a GM panel in `#gm-alerts`.

GM panel tools may include:

- Selecting NPC vassals.
- Adjusting levy percentages.
- Confirming the call.
- Cancelling the call.

The result can create land armies or fleets at home locations and send them toward the rally point depending on the service logic.

If a panel gets stuck:

```text
!gm_diplomacy cancel_call <pending_call_id>
```

## GM Economy

The main GM economy group is:

```text
!gm_econ
```

There is also a legacy/placeholder `!gm_economy` group, but `!gm_econ` is the useful one.

### Economy Mental Model

Gold is stored in separate pockets, not as one flat balance.

- House treasury: central house purse.
- Fief vault: local gold stored at a castle or holding.
- Army/fleet coffer: gold carried by a force.

A house can be rich overall but still fail a command if the needed gold is not in the source pocket that command uses. When troubleshooting, always ask two questions:

- Where is the gold right now?
- Which pocket is this command trying to spend from?

### Player Money Flow For Admins

Players should use the unified `!gold` command for normal money movement.

View the full ledger:

```text
!economy
!bal
!balance
!bank
```

Move gold between pockets:

```text
!gold send <amount> from <source> to <target>
```

Examples:

```text
!gold send 500 from fief Winterfell to army 123
!gold send 1000 from fief Winterfell to fief "Deepwood Motte"
!gold send 300 from army 123 to local_fief
!gold send 200 from treasury to fief Winterfell
```

Valid player source/target forms:

- `fief <name or id>`
- `army <id>`
- `fleet <id>`
- `house <name or id>`
- `treasury` for the player's own house treasury
- `capital` for the player's default/capital fief
- `local_fief` when sending from an army/fleet standing at a fief

Check one money pocket:

```text
!gold check fief Winterfell
!gold check army 123
!gold check treasury
```

The older commands still work as compatibility commands, but `!gold send` is the preferred player workflow:

```text
!transfer_gold 500 army 123
!transfer_from_fief Winterfell 500 army 123
!deposit_gold 300 123
```

Deposit carried army gold into the fief where the army is standing:

```text
!deposit_gold <amount> <army_id>
```

Example:

```text
!deposit_gold 300 123
```

Useful admin translation:

- Funding an army usually means `fief vault -> army coffer`.
- Bringing loot home usually means `army coffer -> local fief vault`.
- Buying units spends from the target fief vault, not from total house wealth.

### GM Money Flow

GMs should use `!gm_gold` for live money movement. It accepts names or numeric IDs and follows the same `send amount from source to target` grammar as the player command.

Audit the whole house ledger:

```text
!gm_gold audit <house_identifier>
!gm_gold economy <house_identifier>
!gm_gold balance <house_identifier>
```

Audit one known entity:

```text
!gm_gold check <source>
```

Force a transfer between supported pockets:

```text
!gm_gold send <amount> from <source> to <target>
```

Supported GM transfer types:

- `fief <name or id>`
- `army <id>`
- `fleet <id>`
- `house <name or id>`
- `local_fief` when sending from an army/fleet standing at a fief

Examples:

```text
!gm_gold send 500 from fief Winterfell to army 300
!gm_gold send 1000 from house Stark to house Tully
!gm_gold send 250 from army 300 to fief Riverrun
!gm_gold send 500 from fief 12 to army 300
```

Use `!gm_gold audit`, `!gm_gold check`, and `!gm_econ fief` first if you are not sure where the money is.

The older GM economy commands still work as compatibility tools:

```text
!gm_econ economy <house_identifier>
!gm_econ check <identifier>
!gm_econ transfer <amount> <source_type> <source_id> <target_type> <target_id>
!gm_econ deposit <amount> <army_id> [target_house_id]
```

### Audit Gold

```text
!gm_econ check <identifier>
```

Examples:

```text
!gm_econ check Stark
!gm_econ check 123
```

The command supports house/fief/army style lookup depending on identifier format.

### Audit Full House Economy

```text
!gm_econ economy <house_identifier>
!gm_econ bal <house_identifier>
!gm_econ balance <house_identifier>
```

Examples:

```text
!gm_econ economy Stark
!gm_econ bal 101
```

### Transfer Gold

```text
!gm_econ transfer <amount> <source_type> <source_id> <target_type> <target_id>
```

Example:

```text
!gm_econ transfer 500 fief 12 army 300
```

### Deposit Army Gold To A Capital Fief

```text
!gm_econ deposit <amount> <army_id> [target_house_id]
```

Example:

```text
!gm_econ deposit 500 123
!gm_econ deposit 500 123 101
```

### Set Tax

```text
!gm_econ set_tax <house_identifier> <percent>
!gm_econ set_vassal_tax <liege_identifier> <percent>
!gm_econ stop_tax <house_identifier>
```

Examples:

```text
!gm_econ set_tax Stark 10
!gm_econ set_vassal_tax Tully 15
!gm_econ stop_tax Frey
```

### Inspect Tax Income

```text
!gm_econ tax_income <house_identifier>
```

### Income Modifiers

```text
!gm_econ set_income global none <value>
!gm_econ set_income region <region_name> <value>
!gm_econ set_income house <house_identifier> <value>
!gm_econ list_income
```

Examples from code comments:

```text
!gm_econ set_income global half
!gm_econ set_income region "The North" 50%
```

### GM Buy Units

```text
!gm_econ buy <House> <Fief Name> <Unit> <Amount> [free]
```

Examples:

```text
!gm_econ buy Stark Winterfell infantry 100
!gm_econ buy Stark Winterfell infantry 100 free
```

Without `free`, the command deducts local fief gold and house manpower. With `free`, it bypasses the cost.

### GM Sell Units

```text
!gm_econ sell <house_identifier> <army_id> <unit_type> <amount>
```

Example:

```text
!gm_econ sell Stark 123 infantry 500
```

### Fix Army Count Sync

```text
!gm_econ fix_sync
```

Recalculates every army's total `troop_count` from its composition.

### Fiscal Year

```text
!year_end
```

Runs the annual economy:

- Fief income.
- Tax flow.
- Integration/modifier effects.
- Reports.

### Other Economy/Admin Events

```text
!punish <house_name> <percent>
!loot <amount> <target_name> <looter_name>
!crown_transfer @Member <amount>
```

Examples:

```text
!punish Stark 10
!loot 5000 "Winterfell" "Greyjoy"
!crown_transfer @Player 1000
```

`!crown_transfer` requires administrator or `Master of Coin`.

## GM Warfare

The GM warfare group is:

```text
!gm_war
```

### GM March

```text
!gm_war march <target_house_id> [army_id] [destination] [units] [mode] [waypoints]
```

The command also supports an interactive flow if not all arguments are provided.

Example:

```text
!gm_war march 101 123 Riverrun all optimal
```

### GM Sail

```text
!gm_war sail <target_house_id> [fleet_id] [destination] [ships] [mode] [waypoints]
```

The command can use interactive setup when arguments are missing.

### GM Stop Movement

```text
!gm_war stop <target_house_id> <army_id>
```

### GM Redirect

```text
!gm_war redirect <target_house_id> <army_id> <new_destination> [new_waypoints]
```

Example:

```text
!gm_war redirect 101 123 Harrenhal
```

### GM Plan Route

```text
!gm_war plan <army_id> <destination> [units=all] [mode=optimal] [waypoints]
```

Example:

```text
!gm_war plan 123 "King's Landing" all optimal
```

This does not move the army.

### GM Split/Merge

```text
!gm_war army_split <house_id> <army_id> <amount> <new_name>
!gm_war army_merge <house_id> <target_id> <source_ids...>
!gm_war merge_all <house_id> <location_name>
```

Examples:

```text
!gm_war army_split 101 123 500 Vanguard
!gm_war army_merge 101 123 124 125
!gm_war merge_all 101 Riverrun
```

Aliases: `!gm_war split`, `!gm_war merge`

### GM Coalition

```text
!gm_war army_coalition <leader_house_id> <new_name> <army_ids...>
!gm_war coalition_disband <target_house_id> <army_id>
```

Aliases: `!gm_war form_coalition`, `!gm_war disband_coalition`

### GM Embark/Disembark

```text
!gm_war embark <target_house_id> <land_army_id> <fleet_id>
!gm_war disembark <target_house_id> <fleet_id>
```

### GM Recruit

```text
!gm_war recruit <target_house_id> <fief_name> <amount>
```

Example:

```text
!gm_war recruit 101 Winterfell 1000
```

### GM Occupy

```text
!gm_war occupy <target_house_id> <army_id>
```

### GM Delete, Force Merge, Reassign Armies

```text
!gm_war delete_army <army_id>
!gm_war force_merge <source_army_id> <target_army_id>
!gm_war reassign <army_id> <target_house_id>
```

These are strong override tools. `force_merge` bypasses normal merge rules, deletes the source army, and moves its troops, treasury, and fleet cargo into the target.
Alias: `!gm_war transfer`

### GM Commander And Casualties

```text
!gm_war set_commander <army_id> <martial> <name>
!gm_war calc_casualties <winner_id> <loser_id> <score> <retreat>
```

Examples:

```text
!gm_war set_commander 123 25 Ser Barristan Selmy
!gm_war calc_casualties 123 456 5-0 true
```

## Fast-Forward Tools

```text
!rush <army_id>
!rush_all [destination]
```

Use these to fast-forward arrivals during testing or admin-managed events.

## Battles And Sieges

### Start Field Battle

```text
!battle <attacker_id> <defender_id> [ambush=none] [defense=none] [terrain=unknown]
```

Examples:

```text
!battle 123 456
!battle 123 456 good minor
!battle 123 456 terrain=river
!battle 123 456 none none hills
```

What happens:

- Armies are loaded.
- Battle state is initialized.
- Terrain, morale, supplies, plans, and phase are tracked.
- Battle control panel appears in `#battle-reports`.
- GM calculation details go to `#gm-alerts`.
- Involved players are notified in quarters.

Supported field plans:

- `aggressive`
- `defensive`
- `flank`
- `feint`
- `cautious`
- `ambush`
- `reserve`

Supported terrain values:

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

Players can set plans for their own side:

```text
!battle_plan <battle_id> <attacker|defender> <plan>
!battle-plan <battle_id> <attacker|defender> <plan>
```

GMs can also use the same command for either side, or set terrain directly:

```text
!battle_terrain <battle_id> <terrain>
!battle-terrain <battle_id> <terrain>
```

### Battle Control Panel

The battle UI supports:

- `Resolve Phase`, which advances the current battle or siege state.
- `Fast Resolve`, for quicker GM-run outcomes.
- GM controls for ending or managing the battle.

The bot tracks:

- Phase.
- Round number.
- Terrain.
- Attacker and defender morale.
- Attacker and defender supply.
- Attacker and defender plans or siege actions.
- Wall integrity for sieges.
- Blockade fleet for coastal sieges.
- Casualties and aftermath.

Field battles usually move through:

- Skirmish.
- Maneuver.
- Main clash.
- Press.
- Rout or pursuit.
- Complete.

Sea battles use the same phased field-battle flow. In simple naval mode, every ship is treated as a ship.

### Start Siege

```text
!siege <attacker_id> <fief_name> [defense=<value>]
```

Examples:

```text
!siege 123 Riverrun
!siege 123 Riverrun defense=minor
```

Sieges are multi-turn. Starting a siege initializes wall integrity, attacker supply, defender supply, morale, default actions, and the siege panel.

Players can set siege actions for their own side:

```text
!siege_action <battle_id> <attacker|defender> <action>
```

Attacker actions:

- `invest`
- `bombard`
- `mine`
- `assault`
- `raid`

Defender actions:

- `repair`
- `sally`
- `ration`
- `counter_mine`
- `ambush`

If no action is set, the attacker defaults to investing and the defender defaults to rationing. Each `Resolve Phase` press resolves one siege turn or the current street-fighting step.

Sieges can progress through:

- Investment and siege turns.
- Breach.
- Street fighting.
- Surrender, collapse, or completion.

### Attach A Blockade

```text
!blockade <fleet_id> <battle_id>
```

This attaches a sea fleet to an active siege as blockade support.

Validation checks include:

- The battle is an active siege.
- The fleet exists and is a sea force.
- The fleet has ships.
- The fleet belongs to the besieging house.
- The fleet is near the fief.
- Nearby hostile fleets are not strong enough to obviously contest the blockade.

Blockades increase pressure on defender supplies and make coastal sieges more dangerous. They should be treated as visible military commitments, not invisible modifiers.

### Resolve Siege Consequences

```text
!resolve_siege <battle_id>
```

This applies the result of a won siege and posts a realm update when successful.

Use this after the siege has actually completed or when the GM has intentionally ruled that the fief falls. It is the ownership/consequence step, not the turn resolver.

### Scouting And Intel

Players can scout known armies/fleets or named areas:

```text
!scout <own_army_id> <target_army_id>
!scout_area <own_army_id> <location_name>
!scout-area <own_army_id> <location_name>
!intel [limit]
```

Scouting reports are intentionally fuzzy. They may reveal rough size, composition, status, morale hints, supply hints, terrain, likely plan, or warnings. Bad reports can be vague or misleading, and failed scouting may notify the target that scouts were sighted.

GM guidance:

- Let scouting inform player planning without giving exact math.
- Treat good reports as useful intelligence, not omniscience.
- Use failed or risky scouting as roleplay fuel when appropriate.
- Do not expose exact battle odds or hidden modifiers to players unless you intentionally want a more board-game-like campaign.

### Auto-Battle Notes

Auto-battle may be triggered by player interceptions.

Expected flow:

- Bot posts an auto-battle pending prompt to `#gm-alerts`.
- GMs can cancel/intervene or proceed.
- If not cancelled, the battle service can progress the encounter through the current phased resolver.

Operational caveat: `app/tasks/battle_tasks.py` defines auto-battle tasks, but the active Celery include list in `app/celery_app.py` does not include it. If auto-battles do not fire, check Celery task registration.

## Gates, Interceptions, And Fog Of War

### Gate Prompts

When an army tries to pass a controlled gate/chokepoint:

- The controlling house or GM receives a prompt.
- Grant resumes the march.
- Deny stops the army and notifies the owner.

If a gate response gets weird, check:

- Owner private channel lock.
- Gate whitelist.
- Pending Celery task.
- GM alerts fallback.

### Interceptions

Movement creates route checkpoints. The bot can detect army collisions or encounters.

When an interception happens:

- Both sides may receive decision prompts.
- Choices can produce battle, meeting, or march-on behavior.
- If choices expire, the resolver handles defaults.

### Player-Created Meetings

Players can now invite more than one person to a private meeting:

```text
!meet @PlayerOne @PlayerTwo @PlayerThree Riverrun War Council
```

Every invited player must accept before the room is created. Meeting channels are named with the location plus a short unique suffix, which prevents repeated meetings between the same people from colliding.

### Fog Of War

Movement can generate FOW/scout reports based on distance, direction, army size, and region.

GMs should watch:

- `#gm-alerts`
- `#general-movements`
- `#army-movements`
- Player quarters fallback warnings.

## Upkeep And Background Jobs

Daily upkeep is configured in Celery beat for midnight UTC.

It can:

- Process army upkeep.
- Apply attrition/bankruptcy behavior.
- Publish bankruptcy alerts.

Make sure the following services are running in production:

- Discord bot.
- PostgreSQL.
- Redis.
- Celery worker.
- Celery beat if scheduled upkeep is desired.

## Deployment Notes

The repo includes Docker/Compose files, but two items should be checked:

- `docker-compose.yml` uses `celery -A app.tasks.celery_app worker`, while the Celery app appears to be `app/celery_app.py`.
- `app/celery_app.py` includes `light_tasks` and `heavy_tasks`, but not `battle_tasks`.

If workers fail to start or auto-battle tasks do not register, inspect those paths first.

## Common Admin Workflows

### New Campaign

```text
!setup_game Baratheon SPLIT
!set_gm @GM true
```

Then wait for player claims:

```text
!approve @Player Stark
!approve @Player Sansa Stark
```

### Fix A Player Who Cannot Use Private Commands

```text
!debug_me
!reset_quarters @Player
```

Ask them to run `!me` in their quarters afterward.

### Audit A House

```text
!gm_info Stark
!gm_econ economy Stark
!gm_war scan_location Winterfell
```

### Start A Manual Battle

```text
!gm_war scan_location Riverrun
!battle 123 456 terrain=river
!battle_plan 12 attacker flank
!battle_plan 12 defender defensive
```

Then use `Resolve Phase` or `Fast Resolve` in the battle panel in `#battle-reports`.

### Run A Siege

```text
!siege 123 Storm's End
!siege_action 14 attacker invest
!siege_action 14 defender ration
!blockade 456 14
```

Use the siege panel to resolve turns. When the siege is complete and the fief should change hands, run:

```text
!resolve_siege 14
```

### Resolve A Stuck Banner Call

```text
!gm_diplomacy cancel_call <pending_call_id>
```

### Fix Army Count Mismatch

```text
!gm_econ fix_sync
```

### End Campaign

```text
!end_game CONFIRM
```

Or, to remove Discord channels/roles too:

```text
!end_game CONFIRM PURGE
```

## Safety Notes

- Prefer normal player commands when possible; GM commands often bypass ownership/location rules.
- Be careful with `delete_army`, `force_merge`/`transfer`, `reassign`, `force_grant`, and `end_game`.
- Use `!gm_info`, `!gm_econ economy`, and `!gm_war scan_location` before destructive changes.
- If a player-facing command fails, check whether they are using the correct private channel.
- If a background event does not fire, check Redis, Celery worker, Celery beat, and task registration.
