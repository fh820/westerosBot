# WesterosBot Player Guide

This guide is for players who are new to WesterosBot. It explains where to use commands, what the main commands do, and the normal flow of play.

All commands use the `!` prefix.

## Quick Start

1. Go to `#claims`.
2. Request a house or character:

```text
!claim Stark
!claim Sansa Stark
```

3. Wait for a GM to approve your claim.
4. After approval, the bot creates your private quarters channel.
5. Use your private quarters for most private commands:

```text
!me
!economy
!army
```

6. Before moving troops, check your army IDs:

```text
!army
```

7. Plan before you move:

```text
!journey
```

8. Issue orders:

```text
!march 123
!sail 456
```

## Where Commands Should Be Used

Most player commands are private and should be used in your locked `*-quarters` channel.

Allowed utility channels include:

- `#bot-testing`
- `#gm-requests`
- `#bot-commands`

The claim command must be used in:

- `#claims`

Public announcements usually appear in channels such as:

- `#news-and-events`
- `#battle-reports`
- `#marriages`
- `#declarations`
- `#general-movements`
- `#army-movements`

## Claiming A House Or Character

### Request A Claim

```text
!claim <House or Character>
```

Examples:

```text
!claim Stark
!claim Jon Snow
```

What happens:

- The bot validates your request.
- A GM approval ticket is sent.
- You cannot submit another claim while one is pending.
- There is a 5 minute cooldown on claim requests.

### After Approval

Once a GM approves you:

- You receive the house role.
- Your private quarters are created.
- Your quarters are locked to your Discord user ID.
- Private reports and alerts will be sent there.

If your quarters break or get deleted, ask a GM to run:

```text
!reset_quarters @You
```

## Your House Dashboard

### View Your Main Report

```text
!me
!info
!stats
```

This shows:

- Your house and character.
- Character stats.
- Total wealth.
- Projected income.
- Manpower.
- Lands and local vaults.
- Armies and fleets.
- Army IDs.
- Army status and location.
- Army composition.
- Carried army gold.
- Cargo indicator for fleets.

Use this command often. It is the easiest way to find your own IDs and current state.

## Economy

Gold is not just one number. It can exist in:

- House treasury.
- Fief vaults.
- Army coffers.

Many commands care where the gold is. A house can have enough total wealth but still fail a command if the gold is not in the right pocket.

Quick mental model:

- House treasury: central house purse.
- Fief vault: local gold stored at a castle or holding.
- Army/fleet coffer: gold carried by a force.

Common money flows:

- Buying units spends from the fief where you buy them.
- Funding an army moves gold from a fief vault to an army coffer.
- Bringing loot home moves gold from an army coffer to a local fief vault.

### View Your Ledger

```text
!economy
!bal
!balance
!bank
```

This shows:

- House treasury.
- Fief vaults.
- Army coffers.
- Total liquid wealth.
- Projected annual tax income.

### View Market Prices

```text
!market
```

Current units:

- `infantry`
- `archers`
- `cavalry`
- `ships`

### Buy Units At A Fief

```text
!buy <Fief Name> <unit_type> <amount>
```

Examples:

```text
!buy Winterfell infantry 500
!buy Storm's End archers 300
!buy White Harbor ships 10
```

Rules:

- You must own the fief.
- Gold is taken from that fief's local vault.
- Land units cost manpower.
- Ships do not cost manpower.
- Land units join or create a garrison.
- Ships join or create a docked fleet.

### Sell Units From An Army

```text
!sell <army_id> <unit_type> <amount>
```

Example:

```text
!sell 123 infantry 200
```

What happens:

- Units are removed from the army.
- You receive gold.
- Land units refund manpower.
- If the army is at a friendly fief, gold goes to that fief.
- If the army is in the field and survives, gold stays with the army.
- If the army is fully disbanded in the field, salvage is sent to a house/fief fallback.

You cannot sell ships that are carrying troops or prisoners.

### Transfer Gold

Use the unified money command:

```text
!gold send <amount> from <source> to <target>
```

Common examples:

```text
!gold send 500 from fief Winterfell to army 123
!gold send 1000 from fief Winterfell to fief "Deepwood Motte"
!gold send 300 from army 123 to local_fief
!gold send 200 from treasury to fief Winterfell
```

Sources and targets can be:

- `fief <name or id>`
- `army <id>`
- `fleet <id>`
- `house <name or id>`
- `treasury` for your own house treasury
- `capital` for your default/capital fief
- `local_fief` when sending from an army/fleet standing at a fief

Check one pocket:

```text
!gold check fief Winterfell
!gold check army 123
!gold check treasury
```

Old transfer commands still work, but `!gold send` is the recommended form.

Compatibility commands:

```text
!transfer_gold 500 army 123
!transfer_from_fief Winterfell 500 army 123
```

If a transfer fails, check whether the source fief actually has the gold. If the gold is in another fief, an army coffer, or the house treasury, it may need to be moved first or handled by a GM.

### Deposit Army Gold Into A Local Fief

```text
!deposit_gold <amount> <army_id>
```

Example:

```text
!deposit_gold 300 123
```

The army must be at a valid local fief.

Use this when an army is carrying loot or travel funds and you want to put that gold back into the place where the army is standing.

The newer equivalent is:

```text
!gold send 300 from army 123 to local_fief
```

### Tax Commands

Set the tax rate you are willing to pay your liege:

```text
!set_tax <percent>
```

Example:

```text
!set_tax 10
```

Set tax expectations for your vassals:

```text
!set_vassal_tax <percent>
```

Stop paying tax:

```text
!stop_tax
```

Stopping tax is a political action. Expect consequences.

## Armies And Fleets

### View Your Forces

```text
!army
```

Use this to find:

- Army IDs.
- Fleet IDs.
- Commander names.
- Troop counts.
- Unit composition.
- Status.
- Location.
- Cargo.
- Treasury carried by armies.

### List Known Locations

```text
!fiefs
!locations
!places
```

Use this when checking spelling for destinations.

### Plan A Journey

```text
!journey
!plan
```

This opens an interactive planning UI. It does not move the army.

Planning can show:

- Destination.
- Estimated travel time.
- Distance.
- Route map.
- Land/sea/optimal mode where available.
- Waypoints where available.

### March A Land Army

```text
!march
!march <army_id>
```

Examples:

```text
!march
!march 123
```

With no ID, the bot opens a selector. With an ID, it opens orders for that army.

Marching can:

- Move all troops or selected troops depending on the modal.
- Use waypoints.
- Generate a route image.
- Schedule arrival.
- Trigger fog-of-war reports.
- Trigger gate checks or interceptions.

### Redirect A Moving Army Or Fleet

```text
!redirect
```

This opens a selector for moving units.

### Stop A Moving Army Or Fleet

```text
!stop <army_id>
!halt <army_id>
```

Example:

```text
!stop 123
```

### Retreat

```text
!retreat <army_id> <destination>
```

Example:

```text
!retreat 123 Riverrun
```

Used after defeat or when ordered by the game state.

## Naval Movement And Cargo

### Sail A Fleet

```text
!sail
!sail <fleet_id>
```

Examples:

```text
!sail
!sail 456
```

The sailing UI can send a fleet empty or with cargo, depending on the modal choices.

### Embark A Land Army Onto A Fleet

```text
!embark <land_army_id> <fleet_id>
```

Example:

```text
!embark 123 456
```

The land army and fleet must be at the same location.

### Disembark From A Fleet

```text
!disembark <fleet_id>
```

Example:

```text
!disembark 456
```

## Army Management

### Split An Army

```text
!split <army_id> <amount> <new_name>
```

Example:

```text
!split 123 500 Northern Vanguard
```

### Merge Armies

```text
!merge <target_id> <source_id_1> <source_id_2> ...
```

Example:

```text
!merge 123 124 125
```

The target army survives; source armies are merged into it.

### Merge Everything At A Location

```text
!merge_all <location_name>
```

Example:

```text
!merge_all Winterfell
```

Merges your stationary units of the same type at that location.

### Form A Coalition Army

```text
!form_coalition <new_name> <army_id_1> <army_id_2> ...
```

Example:

```text
!form_coalition Northern Host 123 456 789
```

Coalitions can include multiple houses. If other player-controlled houses are involved, expect consent prompts.

### Disband A Coalition

```text
!disband <army_id>
```

### Rename A Commander

```text
!set_commander <army_id> <name>
```

Example:

```text
!set_commander 123 Ser Brynden Tully
```

## Recruitment

```text
!recruit <fief_name> <amount>
```

Example:

```text
!recruit Winterfell 1000
```

This recruits troops from your manpower pool into a garrison. If the GM disables manpower/recruitment, normal players cannot use it.

## Occupation

```text
!occupy <army_id>
```

Use this when your army is at an undefended fief you can occupy.

If successful:

- Ownership changes.
- The realm may be notified in `#news-and-events`.

## Diplomacy

### List Vassals

```text
!vassals
```

### Call Land Banners

```text
!call_banners <rally_point>
```

Example:

```text
!call_banners Riverrun
```

This:

- Calls player vassals.
- Prepares NPC vassal levies.
- Sends NPC portions to GM approval.
- Prevents duplicate pending banner calls.

### Call Naval Levies

```text
!call_levies_sea <rally_point>
```

Example:

```text
!call_levies_sea Dragonstone
```

### Disband Levies

```text
!disband_levies
```

### Declare Fealty

```text
!declare_fealty <new_liege>
```

Example:

```text
!declare_fealty Stark
```

### Declare War

```text
!declare_war <target> [reason]
```

Examples:

```text
!declare_war Lannister
!declare_war Lannister For the murder of our kin
```

This mainly posts a declaration; it does not automatically resolve a war.

### Gate Access

```text
!gate_access add <house_name>
!gate_access remove <house_name>
!gate_access list
```

Examples:

```text
!gate_access add Stark
!gate_access remove Frey
!gate_access list
```

Gate access affects controlled passage at gates/chokepoints.

### Meetings

```text
!meet @member [@member2 ...] [location]
```

Examples:

```text
!meet @Robb A private pavilion outside Riverrun
!meet @Robb @Catelyn @Edmure Riverrun War Council
```

Every invited player receives the same proposal. The meeting room is created only after all invited players accept. The room name is unique, using the location plus a short random suffix, so repeated meetings do not collide.

### Marriage And Betrothal

```text
!marry "<Person A>" to "<Person B>"
!betroth "<Person A>" to "<Person B>"
```

Examples:

```text
!marry "Robb Stark" to "Margaery Tyrell"
!betroth "Sansa Stark" to "Willas Tyrell"
```

If the other character is player-controlled, that player must consent. If the other character is NPC-controlled, GMs may need to approve.

## Battles, Interceptions, And Gates

Players do not usually start battles directly. GMs start manual battles, and automatic battle prompts can happen after army interceptions.

When your army encounters another force:

- You may receive a prompt in your quarters.
- You may be asked to choose battle, meeting, or march on.
- Respond before the timer expires.
- If battle starts, reports appear in `#battle-reports`.

When your army hits a controlled gate:

- The controlling house may receive a grant/deny prompt.
- If granted, your march resumes.
- If denied, your army stops and awaits new orders.

## Common Player Workflow

### Starting Out

```text
!claim Stark
!me
!economy
!army
```

### Preparing For War

```text
!market
!buy Winterfell infantry 500
!buy Winterfell archers 300
!army
!set_commander 123 Lord Stark
```

### Moving An Army

```text
!journey
!march 123
```

### Moving By Sea

```text
!army
!embark 123 456
!sail 456
!disembark 456
```

### Calling Vassals

```text
!vassals
!call_banners Riverrun
```

### Managing Money

```text
!economy
!gold send 500 from fief Winterfell to army 123
!gold send 500 from army 123 to local_fief
```

## Tips

- Use `!me`, `!economy`, and `!army` before major decisions.
- Use `!journey` before `!march` or `!sail`.
- Keep important army IDs written down.
- Exact location spelling matters. Use `!fiefs` if unsure.
- If a command fails in public, try your private quarters.
- If a button or modal expires, rerun the command.
- If something looks desynced, ask a GM to audit it.
