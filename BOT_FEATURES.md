# WesterosBot Feature Documentation

WesterosBot is a Discord strategy/RP bot for running a persistent Westeros campaign. It combines house claiming, private player quarters, feudal diplomacy, land and naval warfare, battles, sieges, fief ownership, manpower, localized treasuries, taxes, and GM override tools.

The bot uses prefix commands with `!`, loads every cog in `app/cogs`, stores campaign state in PostgreSQL through SQLAlchemy models, and uses Redis/Celery for delayed and heavier background work such as army arrivals, upkeep, pathfinding, banner processing, gate responses, and auto-battle flow.

## Runtime And Architecture

- **Discord entry point:** `app/bot.py`
  - Loads `.env`, reads `BOT_TOKEN`, creates a `discord.py` bot with message-content intent, initializes the database, creates a `Pathfinder` instance, and loads all cogs under `app/cogs`.
- **Database:** PostgreSQL via SQLAlchemy async/sync engines.
  - Async DB access lives in `app/db/db_manager.py`.
  - Sync DB access for workers/scripts lives in `app/db/sync_db.py`.
- **Background jobs:** Celery and Redis.
  - `app/celery_app.py` defines light and heavy queues.
  - Light tasks handle upkeep, army arrivals, scouting, gates, and player interaction resolution.
  - Heavy tasks handle path generation and banner call processing.
  - Battle tasks also exist for auto-battle progression, though they are not included in the active Celery include list in `app/celery_app.py`.
- **Map/pathfinding:** `app/services/pathfinder_bot_engine.py`, `data/maps/map.jpg`, `data/maps/master_coastal_map.png`, and `master_world_data.json`.
- **UI layer:** `app/ui/*` contains Discord modals, selects, buttons, paginators, battle controls, banner approval panels, gate prompts, coalition consent panels, and movement setup views.

## Core Game Model

The main persistent objects are:

- **Game:** Active campaign for a Discord guild. Stores year, ruling house, world rules, upkeep/manpower toggles, ship capacity, and income modifiers.
- **User:** Discord user or NPC marker, including the database-level GM flag.
- **GamePlayer:** User participation in a game, claimed house/character, primary-player flag, and locked private channel ID.
- **House:** Political faction with liege/dynasty links, treasury, tax rate, manpower, gate whitelist, fiefs, armies, and characters.
- **Fief:** Landed holding with owner, region, type, income, manpower, integration, local treasury, and map coordinates.
- **Character:** Named character with skills, head-of-house marker, and spouse link.
- **Army:** Land army or sea fleet with owner, commander, troop count, composition, cargo, treasury, status, coordinates, destination, ETA, task ID, and coalition flag.
- **ArmyContingent:** Tracks house contributions inside coalition armies.
- **MarchLog:** Timed path checkpoints used for interception/collision detection.
- **Battle:** Active field battle or siege, battle scores, odds, channel/message IDs, start counts, fief target, and winner.
- **PendingBannerCall:** GM-adjustable NPC banner/levy call waiting for approval.
- **PendingInteraction:** Player decision window after army interception.

## Setup And Administration

### `!setup_game [ruling_house=Targaryen] [mode=SPLIT]`

Initializes a new game from `master_world_data.json`.

Features:

- Prevents setup if a game is already active.
- Supports `SPLIT` or `UNIFIED` era modes.
- Accepts an attached `.json` patch that can modify world-data entries before initialization.
- Creates the game, houses, fiefs, characters, armies, treasuries, manpower, and liege structure through `SetupService`.
- Creates core server roles and channels.

Server/channel setup includes:

- `claims`
- `gm-alerts`
- `gm-requests`
- `news-and-events`
- `battle-rumours`
- `battle-reports`
- `rumours`
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

Roles created include:

- `IronThrone`
- `SmallCouncil`
- `Hand of the King`
- `Master of Coin`
- `Master of Whisperers`
- `Master of Ships`
- `Master of Laws`
- `Lord Commander`
- `Grand Maester`

### `!end_game CONFIRM [PURGE]`

Ends the active game and deletes game data in dependency-safe order.

Features:

- Clears house self-references and related battles, march logs, pending interactions, banner calls, contingents, armies, characters, fiefs, players, houses, and the game record.
- With `PURGE`, also deletes major game categories/channels and game roles from Discord.

### Other Admin Commands

- `!set_head @member <house_name>`: assigns a player as head/primary controller of a house.
- `!vacate @member`: removes a player claim, deletes their quarters if found, removes related roles, and deletes the `GamePlayer` record.
- `!debug_me`: diagnoses how the database sees the command author.
- `!set_crown @member`: moves a player into the royal house associated with King's Landing, preserves their quarters link, and adjusts old-house vassalage.
- `!set_heir @member`: assigns a player to the Dragonstone/heir house.
- `!load_scenario <scenario_name>`: applies a scenario patch through `ScenarioService`.
- `!force_grant @member <castle_name>`: force-transfers a fief, armies at the location, vassals of the old owner, and treasury to a player house.
- `!toggleupkeep`: enables/disables daily upkeep and attrition for the active game.
- `!togglemanpower`: enables/disables recruitment/manpower rules.
- `!debug_terrain <x> <y>`: returns the map terrain type at coordinates.
- `!set_gm @member <true|false>`: sets the database GM flag.

## Claiming, Player Identity, And Dashboards

### `!claim <House or Character>`

Players request a house or character claim from the `#claims` channel.

Features:

- Validates the requested house/character.
- Prevents duplicate pending claims per user.
- Applies a 5-minute per-user cooldown.
- Sends a GM approval ticket to `#gm-requests`.
- Supports house claims and character/scion claims.

### `!approve @member <claim_string>`

Admin command that approves a pending claim.

Features:

- Claims either a house or a character depending on the requested name.
- Creates/assigns the appropriate house role.
- Creates private quarters under `Great Houses`.
- Locks the created channel ID to the player in `GamePlayer.private_channel_id`.

### `!deny @member [reason]`

Denies a claim request and attempts to DM the player with the reason.

### `!me`, `!info`, `!stats`

Shows a private, paginated dashboard for the player’s house.

Dashboard includes:

- Character stats.
- Total wealth across house, fiefs, and armies.
- Projected income.
- Manpower and manpower cap.
- Fiefs and local fief vaults.
- Total military strength.
- Armies/fleets, IDs, status, locations, destinations, composition, cargo indicator, and carried gold.

Security behavior:

- Regular players must use their locked private quarters or a recognized utility channel.
- The command can relock a legacy `*-quarters` channel if no channel ID is stored.
- Admins can bypass the house-channel check.

### GM/User Support

- `!gm_info <house_name>` / `!gm_dashboard <house_name>`: GM view of any house dashboard.
- `!debug_access`: shows the author’s current channel ID versus saved private channel ID.
- `!reset_claim_lock @member`: clears a stuck in-memory claim lock.
- `!reset_quarters @member`: clears a player’s saved private channel ID.

## Politics And Royal Offices

### `!coronate @member`

Admin command that assigns the `IronThrone` role to a single member, removes it from others, posts a royal decree, and notifies the player’s quarters.

### `!appoint @member <title>`

King/admin command for appointing Small Council positions.

Supported titles:

- Hand of the King
- Master of Coin
- Master of Whisperers
- Master of Ships
- Master of Laws
- Lord Commander
- Grand Maester

The target receives the specific office role and `SmallCouncil` access.

### `!grant_title <Fief Name> <@User or Character Name>`

House heads can grant a fief they own to another player.

Effects:

- Transfers fief ownership.
- Transfers matching garrisoned armies at that fief.
- Sets the recipient’s house as a vassal of the granting house.
- Optionally transfers upkeep gold if the granting house has enough.
- Notifies the recipient’s quarters.

## Diplomacy

### Banner And Levy Calls

#### `!call_banners <rally_point>`

Player command for calling land vassals to arms.

Features:

- Validates the rally point.
- Prevents duplicate pending banner calls by the same liege.
- Notifies player vassals in their locked private quarters or by DM fallback.
- Prepares NPC vassal troops recursively.
- Creates a persistent GM control panel for NPC contributions.
- GM panel allows levy percentage adjustment and final approval/cancel via `BannerControlView`.

#### `!call_levies_sea <rally_point>`

Naval version of banner calls.

Features:

- Calls vassal fleets/ships instead of land troops.
- Notifies player vassals.
- Creates GM-adjustable NPC sea levy panels.

#### `!disband_levies`

Disbands levies raised through the feudal call system.

### Fealty And War

- `!vassals`: lists a player house’s vassals.
- `!declare_fealty <new_liege>`: changes the player house’s liege.
- `!declare_war <target> [reason]`: posts a war declaration.
- `!gate_access <add|remove|list> [house_name]`: manages which houses may pass a player house’s gate/chokepoint.

### Meetings

#### `!meet @member [@member2 ...] [location]`

Creates a diplomatic meeting proposal for one or more invited players. All invitees must accept before the bot creates a private channel under `Meetings`. Meeting channel names are unique and use the meeting location plus a short random suffix.

### Marriage And Betrothal

#### `!marry "<Person A>" to "<Person B>"`

#### `!betroth "<Person A>" to "<Person B>"`

Features:

- Resolves or creates character records.
- Checks authority over the initiating character.
- If the second character is NPC-controlled, sends GM approval to `#gm-alerts`.
- If controlled by another player, sends a consent proposal to that player’s quarters.
- If self-controlled, executes immediately.
- Posts successful unions to `#marriages`.

### GM Diplomacy

`!gm_diplomacy` is a GM command group.

Subcommands:

- `call_banners <house_id> <rally_point>`: makes an NPC/GM-controlled house call land banners.
- `call_levies_sea <house_id> <rally_point>`: makes an NPC/GM-controlled house call naval levies.
- `declare_fealty <vassal_house_id> <new_liege_name>`: forces fealty.
- `declare_war <aggressor_house_id> <target_house_name> [reason]`: posts a GM-initiated war declaration.
- `set_liege <vassal_house_id> <new_liege_name>`: GM decree version of fealty assignment.
- `cancel_call <pending_call_id>`: cancels a stuck pending banner/levy call and cleans up the GM panel when possible.
- `mass_reassign <old_liege_id> <new_liege_id>`: moves all vassals from one liege to another.

Additional GM command:

- `!gm_gate_access <host_house_id> <add|remove> <target_house_id>`: manages gate access for NPC/GM-controlled houses.

## Economy

The economy is localized. Gold may live in a house treasury, fief vaults, or army coffers. Many commands deliberately move money between those containers instead of treating wealth as one flat balance.

### Market

#### `!market`

Shows troop and ship prices.

Current unit prices:

- Infantry: buy 30, sell 7, manpower cost 1.
- Archers: buy 25, sell 5, manpower cost 1.
- Cavalry: buy 50, sell 12, manpower cost 1.
- Ships: buy 1200, sell 350, manpower cost 0.

### Buying And Selling

#### `!buy <Fief Name> <unit_type> <amount>`

Buys units at a fief the player owns.

Features:

- Supports multi-word fief names.
- Deducts gold from that fief’s local treasury.
- Deducts manpower from the house pool for land units.
- Creates or updates a garrison/fleet at the fief.
- Ships create/use `SEA` fleets with `DOCKED` status; land units create/use `LAND` garrisons.

#### `!sell <army_id> <unit_type> <amount>`

Disbands units from one of the player’s armies for gold.

Features:

- Refunds manpower for land units.
- Deposits gold into the local friendly fief if the army is at home.
- Keeps gold in army coffers if the army survives in the field.
- If the army fully disbands in the field, transfers salvage to a fief or house fallback.
- Prevents selling ships that still carry troops/prisoners.

### Transfers And Deposits

- `!gold send <amount> from <source> to <target>`: preferred player-facing transfer command using one readable grammar.
- `!gold check <source>`: checks one player-controlled money pocket.
- `!check_gold <asset_id>`: checks gold on an asset.
- `!transfer_gold ...`: compatibility command for transfer from the default/capital fief.
- `!transfer_from_fief ...`: compatibility command for transfer from a named fief vault.
- `!deposit_gold <amount> <army_id>`: compatibility command for depositing carried army gold into an eligible storage destination.
- `!crown_transfer @member <amount>`: Master of Coin/admin transfers from King's Landing vault to a target house’s fief.

Unified transfer examples:

```text
!gold send 500 from fief Winterfell to army 123
!gold send 1000 from fief Winterfell to fief "Deepwood Motte"
!gold send 300 from army 123 to local_fief
!gold check treasury
```

### Taxes And Annual Economy

- `!year_end`: GM command that runs the fiscal year.
- `!set_tax <percent>`: sets the player house’s own tax rate.
- `!set_vassal_tax <percent>`: sets rates for vassals.
- `!stop_tax`: stops paying taxes.
- `!punish <house_name> <percent>`: applies desertion/punishment mechanics.
- `!loot <amount> <target_name> <looter_name>`: moves/records looting.
- `!economy`, `!bal`, `!balance`, `!bank`: shows the player’s financial ledger, including house treasury, fief vaults, army coffers, total wealth, and projected annual tax income.

Fiscal-year logic:

- Calculates fief income, using integration and income modifiers.
- Applies taxes up the feudal tree.
- Stores wealth in fief/house containers.
- Can calculate projected tax income per house.
- Supports global, region, and house-level income modifiers.

### GM Economy

`!gm_gold` is the recommended live GM money movement group. `!gm_econ` remains the broader GM economy/admin group.

Recommended GM gold commands:

- `send <amount> from <source> to <target>`: GM transfer between supported containers using names or IDs.
- `check <source>`: checks one money pocket.
- `audit <house_identifier>` / aliases `economy`, `bal`, `balance`: GM audit of a house ledger.

Examples:

```text
!gm_gold send 500 from fief Winterfell to army 123
!gm_gold send 1000 from house Stark to house Tully
!gm_gold send 300 from army 123 to local_fief
!gm_gold audit Stark
```

Subcommands:

- `check <identifier>`: audits a house/fief/army.
- `transfer ...`: compatibility GM gold transfer between supported containers.
- `deposit ...`: compatibility GM deposit into storage.
- `set_tax <house_identifier> <percent>`: set a house tax rate.
- `set_vassal_tax <liege_identifier> <percent>`: set vassal tax rates.
- `stop_tax <house_identifier>`: stop a house paying taxes.
- `tax_income <house_identifier>`: show projected tax income.
- `set_income <scope> <target> <modifier>`: set global/region/house income modifiers.
- `list_income`: list active income modifiers.
- `sell ...`: GM disbands units from a house/army.
- `buy <House> <Fief Name> <Unit> <Amount> [free]`: GM recruits units for a house.
- `fief <fief_name>`: lookup fief IDs and owners.
- `economy <house_identifier>` / aliases `bal`, `balance`: GM audit of a house ledger.
- `fix_sync`: recalculates every army’s `troop_count` from composition.

There is also a placeholder/legacy `!gm_economy` group that appears to mostly redirect users toward the newer GM economy tooling.

## Warfare, Movement, And Map Features

### Location And Planning

#### `!fiefs`, `!locations`, `!places`

Lists all known fiefs/locations, usually paginated.

#### `!journey`, `!plan`

Player planning command that opens a UI to select an army and plan a route without moving it.

Planning supports:

- Map-aware pathfinding.
- Army-size-sensitive travel time.
- Travel modes such as land, sea, or optimal routing.
- Waypoints.
- Map image generation showing the planned path.

#### `!gm_war plan <army_id> <destination> [units=all] [mode=optimal] [waypoints]`

GM planning equivalent that simulates a journey without issuing movement orders.

### Land Movement

#### `!march [army_id]`

Issues march orders.

Features:

- If no army ID is passed, opens an army selector.
- Uses a modal for destination, units, mode, and waypoints.
- Generates a path and ETA.
- Sets army status to `MARCHING`.
- Saves movement timing and Celery task ID.
- Creates MarchLog checkpoints for interception checks.
- Sends movement/fog-of-war reports.

#### `!redirect`

Redirects a moving army/fleet through a selector/modal workflow.

#### `!stop <army_id>` / `!halt <army_id>`

Stops a moving army.

#### `!retreat <army_id> <destination>`

Orders a retreat to a destination, including path image output when available.

### Sea Movement

#### `!sail [fleet_id]`

Issues sailing orders.

Features:

- If no fleet ID is passed, opens fleet selection.
- Uses a modal for destination/waypoints and a cargo modal if loading troops.
- Supports empty sailing or sailing with cargo.
- Uses ship capacity rules.
- Finds coastal/landing points where needed.
- Sets fleet status to `SAILING`.
- Schedules arrival and movement reports.

#### `!embark <land_army_id> <fleet_id>`

Loads a land army onto a fleet.

#### `!disembark <army_id>`

Unloads an embarked army/fleet cargo to land.

### Army Management

#### `!army`

Shows player army/fleet details.

#### `!split <army_id> <amount> <new_name>`

Splits a portion of troops from an army into a new army.

#### `!merge <target_id> <source_ids...>`

Merges multiple armies into a target army.

#### `!merge_all <location_name>`

Merges all of the player’s stationary units of the same type at a location.

#### `!form_coalition <new_name> <army_ids...>`

Creates a coalition army from multiple house contributions.

Features:

- Tracks contingents by original house.
- Uses consent UI when multiple owners are involved.
- Preserves composition/cargo/treasury contribution data.

#### `!disband <army_id>`

Disbands a coalition army and returns or resolves its contingents.

#### `!set_commander <army_id> <name>`

Renames a player-owned army commander.

### Recruitment

#### `!recruit <fief_name> <amount>`

Recruits troops from manpower at a fief through warfare service rules. The command is blocked for non-GMs if the game’s manpower system is disabled.

### Occupation And Conquest

#### `!occupy <army_id>`

Occupies a fief if conditions allow.

Features:

- Checks that the fief is undefended.
- Changes ownership.
- Posts to `#news-and-events` on success.

### World Rules

`!worldrule` is a command group for map travel rules.

Subcommands:

- `setbridge <bridge_name> <open|closed>`: toggles named crossings such as Twins/Ruby Ford/Bitterbridge depending on implementation.
- `setrivers <passable|impassable>`: toggles river crossing behavior.
- `setsea <on|off>`: toggles sea travel.

These update fields on the active `Game` and feed pathfinding/travel validation.

### Fast-Forward/Admin Movement Helpers

- `!rush <army_id>`: admin helper for fast-forwarding an army’s arrival.
- `!rush_all [destination]`: admin helper for fast-forwarding multiple moving armies, optionally filtered by destination.

### GM Warfare

`!gm_war` is the main GM warfare group.

Subcommands:

- `march <target_house_id> ...`: issue NPC/GM land movement orders.
- `sail <target_house_id> ...`: issue NPC/GM naval movement orders.
- `stop <target_house_id> <army_id>`: stop an NPC/GM army.
- `split ...`: GM split army.
- `merge ...`: GM merge armies.
- `form_coalition ...`: GM form a coalition army.
- `disband_coalition <target_house_id> <army_id>`: disband a coalition.
- `embark ...`: GM embark a land army.
- `disembark <target_house_id> <fleet_id>`: GM disembark cargo.
- `recruit <target_house_id> <fief_name> <amount>`: GM recruit for a house.
- `occupy <target_house_id> <army_id>`: GM force an army to occupy if valid.
- `redirect <target_house_id> <army_id> <new_dest_name> [new_waypoints]`: redirect NPC/GM army or fleet.
- `delete_army <army_id>`: force-delete an army and cargo.
- `transfer <source_army_id> <target_army_id>`: instant transfer of one army into another.
- `reassign <army_id> <target_house_id>`: change army owner without changing status.
- `plan <army_id> <destination> [units] [mode] [waypoints]`: simulate path.
- `set_commander <army_id> <martial> <name>`: sets commander name and martial score.
- `calc_casualties <winner_id> <loser_id> <score> <retreat>`: manually applies battle casualties.
- `scan_location <location_name or coords>`: lists all armies/garrisons at a location.
- `merge_all <target_house_id> <location_name>`: merge all stationary units for a house at a location.

## Fog Of War, Interceptions, Gates, And Arrivals

The bot uses Redis pub/sub and Celery tasks to bridge long-running game mechanics back into Discord.

### Army Arrivals

When a march/sail task reaches ETA:

- Army location is updated.
- Status returns to idle/docked/garrisoned as appropriate.
- Arrival notifications are posted to the owner’s locked quarters.
- Public movement channels may receive general reports.
- FOW summaries are generated for nearby/affected parties.

### Fog Of War

`WarfareService.get_fog_of_war_message` calculates what other houses might observe based on distance, region, direction, army size, and threshold rules. This is used around movement and GM reporting.

### Interceptions

The movement system stores MarchLog checkpoints and checks routes for collisions or proximity.

When a potential encounter is found:

- Both armies may be halted at the intercept point.
- A `PendingInteraction` is created with an expiry.
- Each player receives an `InteractionView`.
- Choices include battle, meeting, or marching on.
- If choices resolve to battle, auto-battle prompting can begin.
- If choices resolve to meeting, a private meeting channel can be created.
- If the interaction expires, the resolver task handles default behavior.

### Gate/Chokepoint Access

Gate logic checks whether armies crossing controlled chokepoints have permission.

Features:

- Host houses maintain a `gate_whitelist`.
- Unauthorized passage creates a `GateActionView` for the controlling player/GM.
- The host can grant or deny passage.
- Granted passage resumes the paused march.
- Denied passage halts the army and notifies its owner or GM fallback.

## Battles And Sieges

### `!battle <attacker_id> <defender_id> [ambush=none] [defense=none]`

Admin command to start a field battle.

Features:

- Stops involved armies.
- Calculates current odds based on troop strength, composition, commander martial, ambush, terrain/defense settings, and battle-power logic.
- Posts a public battle control panel to `#battle-reports`.
- Sends calculation details to `#gm-alerts`.
- Notifies involved players in locked quarters.
- Stores public and GM message IDs on the `Battle` record.

### Battle Control UI

`BattleControlView` provides:

- Roll next round.
- Set/update modifiers through `ModifierModal`.
- End battle.

Round resolution:

- Rolls against current odds.
- Updates attacker/defender score.
- Applies round casualties.
- Updates battle report embeds.

Manual aftermath:

- Determines winner/loser.
- Applies rout losses and destruction rules.
- Handles army survival/deletion.
- Can produce battle-damage summary data.

### Auto-Battle

Auto-battle is triggered by player interaction outcomes.

Flow:

- A pending auto-battle prompt is sent to `#gm-alerts`.
- GMs get a 15-minute intervention window through `AutoBattleControlView`.
- They can cancel auto-battle and take manual control, or proceed immediately.
- If allowed, Celery battle tasks run rounds and final aftermath.
- Round and final reports are published to Redis and posted to `#battle-reports`.

Important implementation note: `app/tasks/battle_tasks.py` defines auto-battle Celery tasks, but the active `app/celery_app.py` include list only includes `light_tasks` and `heavy_tasks`. If auto-battle tasks are expected to run in production, confirm that workers import/register `battle_tasks`.

### `!siege <attacker_id> <fief_name> [defense=<minor|...>]`

Admin command to start a siege against a fief.

Features:

- Finds the fief and defenders.
- Starts a `Battle` with `battle_type="SIEGE"`.
- Supports a defense bonus string.
- Posts public siege controls and GM calculations.

### `!resolve_siege <battle_id>`

Applies consequences for a won siege.

Effects may include:

- Ownership changes.
- Garrison/defender handling.
- Public result post to `#news-and-events`.

## Background Upkeep And Attrition

### Daily Upkeep

Configured in Celery beat as `daily-upkeep-every-24h` at midnight UTC.

Behavior:

- Iterates active games.
- Runs game upkeep only when `Game.upkeep_enabled` is true.
- Processes army upkeep costs/attrition through `light_tasks`.
- Publishes bankruptcy alerts when relevant.

### Manpower System

Manpower is calculated from holdings and can be toggled by GMs.

Used by:

- Recruitment.
- Buying land units.
- Selling/disbanding land units for manpower refund.

## UI Components

The bot relies heavily on Discord UI components:

- `Paginator`: generic embed pagination.
- `SimplePaginationView`: fief/economy pagination.
- `ProposalView`: accept/decline proposals for marriage, betrothal, meetings, or GM approvals.
- `BannerControlView`, `VassalSelect`, `PercentageModal`: GM banner/levy approval and contribution adjustment.
- `BattleControlView`, `ModifierModal`: battle rounds, modifiers, and ending battles.
- `AutoBattleControlView`: cancel/proceed controls for auto-battle.
- `MarchModal`, `ArmySelectView`, `JourneyModal`, `JourneyArmySelectView`, `DirectMarchView`: player land movement.
- `SailSetupModal`, `SailCargoModal`, `SailContinueView`, `FleetSelectView`, `DirectSailView`: player naval movement and cargo.
- `GMMarchModal`, `GMMarchArmySelectView`, `DirectGMMarchView`: GM land movement setup.
- `GMSailSetupModal`, `GMSailCargoModal`, `GMSailContinueView`, `GMFleetSelectView`, `DirectGMSailView`: GM naval movement setup.
- `RedirectModal`, `RedirectSelectView`: redirecting moving units.
- `GateActionView`: grant/deny controlled passage.
- `InteractionView`: interception choice UI.
- `CoalitionConsentView`: multi-house coalition consent.
- `TransactionView`: accept/reject money transfers.

## Scenarios And Data Patches

The repo includes scenario files:

- `app/scenarios/roberts_rebellion.json`
- `app/scenarios/dance_of_dragons.json`

`!load_scenario <scenario_name>` applies historical scenario changes through `ScenarioService`.

`!setup_game` also supports ad hoc attached JSON patch files. Repo-level patch examples now live under `data/patches/`, including files such as `north_update.json`, `riverland_update.json`, `essos_update.json`, and others.

## Deployment

The repo includes:

- `Dockerfile`
- `docker-compose.yml`
- `requirements.txt`

Compose services:

- `bot`: runs `python -m app.bot`.
- `worker`: intended to run Celery.
- `postgres`: PostgreSQL 15.
- `redis`: Redis 7.

Potential deployment issue: `docker-compose.yml` uses `celery -A app.tasks.celery_app worker`, but the Celery app file in this repo is `app/celery_app.py`, not `app/tasks/celery_app.py`. Unless there is an untracked/missing module, that command should likely be reviewed.

## Access Control Summary

- Most player commands require being in a locked private quarters channel or allowed utility channel via `is_in_house_channel`.
- Discord administrators bypass many player checks.
- Some GM groups check database-level `User.is_gm`.
- Some commands use Discord administrator permission directly.
- Player private notifications prefer `GamePlayer.private_channel_id`, with fallbacks to DMs or GM alerts in several systems.

## Notable Caveats Found During Review

- `app/celery_app.py` includes `light_tasks` and `heavy_tasks`, but not `battle_tasks`, despite auto-battle tasks existing.
- `docker-compose.yml` points Celery at `app.tasks.celery_app`, while the active Celery app module appears to be `app/celery_app.py`.
- `app/cogs/economy.py` defines `deposit_gold` twice with the same command name in the class; in Python, the later method overrides the earlier class attribute. This may be intentional replacement code, but it is worth cleaning up.
- Some comments and console strings contain mojibake/encoding artifacts. Functionally harmless, but they make logs and docs harder to read.
- `scripts/strip_comments.py` is currently tracked in git.
