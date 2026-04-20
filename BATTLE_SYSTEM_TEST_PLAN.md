# Battle System Test Plan

Use this checklist to test the battle, siege, blockade, and scouting revamp in a live test server or local Discord test guild.

This plan assumes:

- A game is already active.
- You have GM/admin permissions.
- You have at least two houses with armies.
- You can see `#battle-reports`, `#gm-alerts`, and player quarters.
- You can use test armies without worrying about campaign damage.

Do not run these tests on important campaign armies unless you are ready to undo or repair the results.

## What To Record During Tests

For every battle or siege, write down:

- Battle ID.
- Attacker and defender army IDs.
- Starting troop counts and composition.
- Starting odds shown in `#gm-alerts`.
- Each `#gm-alerts` audit message after resolving a phase or siege turn.
- Final survivors from `!army`.

This is especially important now that the bot posts math audits. If a result looks wrong, the audit should show whether the issue came from unit BP, commander martial, terrain, plans, morale, supply, siege actions, or casualty tables.

## Before Testing

### 1. Confirm Bot Health

Run:

```text
!me
!army
!economy
```

Expected:

- Commands respond normally.
- Your private quarters work.
- Armies and fleets show IDs, locations, and compositions.

### 2. Find Test Armies

As GM, scan a location with armies:

```text
!gm_war scan_location <location>
```

Example:

```text
!gm_war scan_location Riverrun
```

Record:

- Attacker army ID.
- Defender army ID.
- Any nearby fleet ID.
- Any coastal fief name for siege/blockade tests.

### 3. Optional: Create Clean Test Forces

If your campaign has no disposable armies, create or adjust test forces with existing GM tools before running destructive combat tests.

Good test setup:

- One land army with mixed infantry, archers, and cavalry.
- One opposing land army.
- One fleet owned by the attacking house.
- One coastal fief with a garrison.

## Test 1: Start A Field Battle

Command:

```text
!battle <attacker_army_id> <defender_army_id> terrain=plains
```

Example:

```text
!battle 123 456 terrain=plains
```

Expected:

- Bot confirms the battle started.
- A battle panel appears in `#battle-reports`.
- GM details appear in `#gm-alerts`.
- The `#gm-alerts` initial odds embed includes unit BP, commander martial divided by 3, ambush/defense bonus, outnumber bonus, totals, baseline odds, and starting current odds.
- Involved players receive private notifications if their quarters are configured.
- The panel shows battle state such as phase, terrain, morale, supply, and plans.

Fail signs:

- No battle panel appears.
- The command says channels are missing.
- The battle starts but has blank phase/morale/supply fields.
- Players are not notified and no fallback/error appears.

## Test 2: Set Battle Plans

As the attacker or GM:

```text
!battle_plan <battle_id> attacker aggressive
```

As the defender or GM:

```text
!battle_plan <battle_id> defender defensive
```

Alias test:

```text
!battle-plan <battle_id> attacker flank
```

Expected:

- Valid plans are accepted.
- The battle panel or later phase report reflects the chosen plans.
- Non-GM players can only set plans for their own side.
- GMs can set either side.

Invalid plan test:

```text
!battle_plan <battle_id> attacker nonsense
```

Expected:

- Bot rejects it and lists valid plans.
- Existing battle state is not damaged.

Permission test:

- Have a player from an uninvolved house try to set a plan.

Expected:

- Bot rejects the command.

## Test 3: Change Battle Terrain

As GM:

```text
!battle_terrain <battle_id> forest
```

Alias:

```text
!battle-terrain <battle_id> hills
```

Expected:

- Terrain changes successfully.
- Later phase reports use the new terrain context.

Invalid terrain test:

```text
!battle_terrain <battle_id> moon
```

Expected:

- Bot rejects it and lists valid terrain.

## Test 4: Resolve Field Battle Phases

In `#battle-reports`, press:

```text
Resolve Phase
```

Repeat until the battle completes.

Expected phase flow:

- Skirmish.
- Maneuver.
- Main clash.
- Press.
- Rout or pursuit.
- Complete.

Expected after each press:

- A new phase result is posted or the panel updates.
- `#gm-alerts` receives a phase odds audit.
- The audit shows starting current odds, morale adjustment, supply adjustment, plan modifier, terrain/composition modifier, final phase target, and roll.
- Casualties are applied gradually.
- Morale changes.
- The battle advances its phase or completes.
- Armies are not duplicated.
- Troop counts do not go negative.
- If the battle ends, `#gm-alerts` receives an aftermath casualty audit.

After completion, run:

```text
!army
```

Expected:

- Surviving armies reflect casualties.
- Destroyed or defeated armies behave consistently with the result.
- The final survivor numbers are consistent with phase losses plus the aftermath audit.

Manual math spot-check:

- Pick one phase audit.
- Confirm `phase_odds = current_odds + morale_adjustment + supply_adjustment + plan_modifier + terrain_modifier`, clamped to `5-95`.
- Confirm the attacker won only if `roll <= phase_odds`.
- Confirm reported casualties roughly match `int(current_troops * loss_pct * plan_multiplier)`, with a minimum of 1 if the percentage is greater than 0.
- Confirm the battle panel tracks phase wins out of 5, not 3.

## Test 5: Fast Resolve Field Battle

Start another test battle:

```text
!battle <attacker_army_id> <defender_army_id> terrain=river
```

Set plans:

```text
!battle_plan <battle_id> attacker cautious
!battle_plan <battle_id> defender defensive
```

In the battle panel, press:

```text
Fast Resolve
```

Expected:

- The battle resolves without needing every manual phase press.
- Final result is posted.
- `#gm-alerts` receives one audit per resolved phase.
- `#gm-alerts` receives one aftermath audit if the battle ends.
- Casualties and aftermath are applied once.
- The battle does not remain stuck in an active phase.

## Test 6: Force-End Before Combat

Start a fresh field battle:

```text
!battle <attacker_army_id> <defender_army_id> terrain=plains
```

Do not press `Resolve Phase`.

In the battle panel, press:

```text
End Battle (Force)
```

Expected:

- The battle is cancelled.
- No rout casualties are applied.
- No commander is captured or killed.
- No loot is transferred.
- Both armies keep the same troop counts they had when the battle started.
- `#gm-alerts` receives a forced-end cancellation audit showing `0` casualties.

Fail signs:

- A 0-0 battle declares a victor.
- One side is wiped out.
- A commander is captured or killed.
- An aftermath casualty audit appears instead of a forced-end cancellation audit.

## Test 7: Start A Sea Battle

Use two fleet IDs.

```text
!battle <attacker_fleet_id> <defender_fleet_id> terrain=open_sea
```

Set plans:

```text
!battle_plan <battle_id> attacker aggressive
!battle_plan <battle_id> defender cautious
```

Press `Resolve Phase`.

Expected:

- Battle starts as a naval/sea battle.
- Initial `#gm-alerts` details use ship BP.
- Ships are treated in simple mode.
- The same phased system works.
- Casualties reduce ships sensibly.
- Cargo/transported troops are not silently duplicated or lost unless the battle result calls for it.
- If the fleet has cargo, cargo should reduce proportionally when ships are lost.

Also test:

```text
!battle <fleet_id> <land_army_id>
```

Expected:

- Bot rejects mismatched land-vs-sea field battle if they are not valid combatants.

## Test 8: Start A Siege

Use an attacking land army at or near a fief.

```text
!siege <attacker_army_id> <fief_name>
```

Example:

```text
!siege 123 Storm's End
```

Optional defense argument:

```text
!siege 123 Storm's End defense=minor
```

Expected:

- Siege starts in `#battle-reports`.
- GM details appear in `#gm-alerts`.
- The `#gm-alerts` initial siege odds embed shows attacker BP, attacker martial/3, defender BP, defender martial/3, defense bonus, raw odds formula, clamp, and starting wall/supply/morale state.
- Siege panel shows wall integrity, morale, supplies, phase, and actions.
- Default attacker action is investment/starvation.
- Default defender action is rationing.

Fail signs:

- Siege starts without wall integrity.
- Supplies or morale are blank.
- The target fief cannot be found despite exact spelling.

## Test 9: Set Siege Actions

Attacker action:

```text
!siege_action <battle_id> attacker bombard
```

Defender action:

```text
!siege_action <battle_id> defender repair
```

Expected:

- Valid actions are accepted.
- Players can only set actions for their own side.
- GMs can set either side.
- The next siege turn uses those actions.

Invalid action tests:

```text
!siege_action <battle_id> attacker repair
!siege_action <battle_id> defender bombard
!siege_action <battle_id> attacker nonsense
```

Expected:

- Bot rejects invalid side/action combinations.
- Existing siege state remains intact.

## Test 10: Resolve Siege Turns

Press `Resolve Phase` on the siege panel several times.

Test these combinations:

```text
!siege_action <battle_id> attacker invest
!siege_action <battle_id> defender ration
```

```text
!siege_action <battle_id> attacker bombard
!siege_action <battle_id> defender repair
```

```text
!siege_action <battle_id> attacker mine
!siege_action <battle_id> defender counter_mine
```

```text
!siege_action <battle_id> attacker assault
!siege_action <battle_id> defender ambush
```

Expected:

- Wall integrity changes.
- Defender supplies change.
- Attacker supplies change.
- Morale changes.
- Casualties can occur.
- `#gm-alerts` receives a siege walls turn audit after each resolved walls turn.
- The audit shows action effects, special interactions, blockade effects if any, wall random roll, old and new wall/supply/morale values, casualty percentages, and result check.
- Actions reset or remain understandable for the next turn.
- The siege does not complete too quickly unless morale, supply, walls, or GM action justify it.

Manual math spot-check:

- For one turn, verify wall delta equals attacker wall effect + defender wall effect + random wall roll.
- Verify supply and morale changes match the action tables.
- For `assault`, verify casualty rates are based on wall integrity after wall changes are applied.
- For `bombard` or `mine`, verify defender loses 1% directly.
- For `sally`, verify attacker loses 4% and defender loses 3%.

## Test 11: Breach And Street Fighting

Drive wall integrity down using repeated pressure:

```text
!siege_action <battle_id> attacker bombard
!siege_action <battle_id> defender ration
```

Press `Resolve Phase` until walls fail or the siege changes state.

Expected:

- Siege moves to breach/street fighting instead of instantly doing ownership transfer.
- Street fighting can be resolved from the panel.
- `#gm-alerts` receives a street-fighting odds audit.
- Casualties are higher than normal siege turns.
- Final siege result is clear.
- If the siege fight concludes, `#gm-alerts` receives an aftermath casualty audit.

After the siege is won, run:

```text
!resolve_siege <battle_id>
```

Expected:

- Ownership/consequences are applied only after this command or explicit GM resolution.
- A realm update posts when successful.

## Test 12: Supply Or Morale Collapse

Start a fresh siege and choose starvation pressure:

```text
!siege_action <battle_id> attacker invest
!siege_action <battle_id> defender ration
```

Resolve several turns.

Expected:

- Defender supplies trend downward over time.
- Morale pressure increases.
- Siege can end by surrender/collapse without needing total wall destruction.
- `#gm-alerts` audit explicitly states whether the result came from defender collapse, attacker collapse, wall breach, or siege continuing.

Check:

- Defender supplies do not go below sensible bounds in the display.
- Morale does not show impossible values.

## Test 13: Attach A Blockade

Use a coastal siege and an attacking fleet near the fief.

```text
!blockade <fleet_id> <battle_id>
```

Expected:

- Bot accepts a valid attacking fleet.
- Siege panel shows blockade support.
- Later siege turns apply extra coastal pressure.
- The next `#gm-alerts` siege turn audit includes blockade pressure: defender supply `-8` and defender morale `-2`.

Invalid blockade tests:

```text
!blockade <land_army_id> <battle_id>
!blockade <enemy_fleet_id> <battle_id>
!blockade <far_away_fleet_id> <battle_id>
!blockade <fleet_id> <field_battle_id>
```

Expected:

- Bot rejects invalid blockades with a useful reason.
- Existing siege state is not damaged.

Contested blockade test:

- Put a hostile fleet near the same coastal fief.
- Try to attach the blockade.

Expected:

- If the hostile fleet is strong enough or positioned to contest, the bot should reject or warn according to current validation.

## Test 14: Scout A Known Force

Use one of your armies or a GM/admin account:

```text
!scout <own_army_id> <target_army_id>
```

Expected:

- Bot returns a scout report embed.
- Report has a confidence level.
- Report includes fuzzy information, not exact battle math.
- Report may include rough strength, composition, status, terrain, morale, supply, likely plan, or warnings.
- Report footer says intel is fuzzy and expires.

Permission test:

- Have a player scout using an army they do not own.

Expected:

- Bot rejects the command.

## Test 15: Scout An Area

Command:

```text
!scout_area <own_army_id> <location_name>
```

Alias:

```text
!scout-area <own_army_id> <location_name>
```

Expected:

- Bot returns detected forces in or near that location.
- Report is fuzzy.
- Empty areas return no clear forces found.
- Bad location spelling produces a clear error.

## Test 16: Review Intel

Command:

```text
!intel
!intel 10
```

Expected:

- Recent scout reports appear.
- Limit is capped sensibly.
- Players see their own house reports.
- GMs can inspect reports more broadly if implemented that way.

## Test 17: Scouting Alert Risk

Run several scouts against an enemy force, especially from poor conditions or with a weak scouting army.

Expected:

- Some failed or poor scouting attempts may alert the target in private quarters.
- Alerts should not reveal exact scout details.
- Alerts should not spam excessively under normal use.

## Test 18: Player Ownership Rules

For each player-facing command, test with:

- Correct attacker player.
- Correct defender player.
- Uninvolved player.
- GM/admin.

Commands:

```text
!battle_plan <battle_id> attacker aggressive
!battle_plan <battle_id> defender defensive
!siege_action <battle_id> attacker invest
!siege_action <battle_id> defender repair
!scout <own_army_id> <target_army_id>
!scout_area <own_army_id> <location_name>
```

Expected:

- Correct side players can command their side.
- Uninvolved players are rejected.
- GMs can override where intended.

## Test 19: GM Math Audit Trail

Run this test after completing at least one field battle and one siege.

Field battle audit checklist:

- `!battle` start posts an initial odds embed in `#gm-alerts`.
- Each manual `Resolve Phase` posts a phase audit in `#gm-alerts`.
- `Fast Resolve` posts a separate audit for each phase it resolves.
- `Set Modifiers` posts an odds recalculation audit in `#gm-alerts`.
- Battle conclusion posts an aftermath casualty audit in `#gm-alerts`.

Siege audit checklist:

- `!siege` start posts an initial siege odds embed in `#gm-alerts`.
- Each walls-phase `Resolve Phase` posts a siege walls turn audit in `#gm-alerts`.
- A blockade appears in the next siege turn audit if attached.
- Street fighting posts a street-fighting odds audit in `#gm-alerts`.
- Siege conclusion posts an aftermath casualty audit in `#gm-alerts`.

Expected:

- GM audit embeds are detailed enough to explain how odds or casualties were calculated.
- Public `#battle-reports` remains readable and does not expose the full math trail to players.
- Audit messages use the correct Battle ID.
- Audit messages do not appear in the wrong battle's GM thread/channel.

Fail signs:

- Public battle report appears, but no GM audit appears.
- GM audit is missing major inputs such as morale, supply, terrain, action effects, or casualty percentage.
- Fast resolve skips audits for intermediate phases.
- Aftermath occurs but no casualty table/index audit appears.

## Test 20: Persistence After Restart

Start one active battle and one active siege.

Set:

```text
!battle_plan <battle_id> attacker feint
!battle_plan <battle_id> defender cautious
!siege_action <siege_id> attacker mine
!siege_action <siege_id> defender counter_mine
```

Restart the bot.

Expected:

- Existing battle records still have phase, round, terrain, morale, supply, plans, wall integrity, and blockade fields.
- Commands can still resolve the battle/siege after restart.
- New post-restart resolution still posts GM math audits.
- No migration error appears on startup.

## Test 21: Documentation Sanity

Read:

- `PLAYER_GUIDE.md`
- `ADMIN_GUIDE.md`
- `BATTLE_SYSTEM_CHANGES.md`
- `BATTLE_MATH_REFERENCE.md`

Expected:

- Commands in the docs match bot commands.
- Player docs do not reveal exact formulas.
- Admin docs explain where GM control begins and player agency ends.
- `BATTLE_MATH_REFERENCE.md` matches the GM audit language closely enough to help debug a disputed result.

## Quick Regression Checklist

After all tests, run or confirm:

```text
!army
!me
!economy
!journey
```

Expected:

- Core player commands still work.
- Armies/fleets are not duplicated.
- Destroyed units do not reappear.
- Casualty results match the battle reports closely enough to be trusted.
- GM math audits exist for initial odds, phase/turn odds, and aftermath.
- Fief ownership only changes after proper siege consequence resolution.

## Bugs To Record

For each bug, write down:

- Command used.
- Battle or siege ID.
- Army/fleet IDs.
- Fief/location name.
- Channel where it happened.
- Screenshot or copied bot response.
- What you expected.
- What happened instead.

Good bug report example:

```text
Command: !siege_action 14 attacker bombard
Battle ID: 14
Army ID: 123
Fief: Storm's End
Expected: attacker action set to bombard
Actual: bot accepted command but panel still showed invest after refresh
```

## Suggested Full Test Order

1. Start a land battle.
2. Set plans.
3. Resolve each phase manually.
4. Start another land battle and fast resolve it.
5. Start and immediately force-end a no-combat battle.
6. Start a sea battle.
7. Start a siege.
8. Test siege actions.
9. Attach a blockade.
10. Resolve siege turns until breach or collapse.
11. Resolve siege consequences.
12. Test scouting known forces.
13. Test scouting an area.
14. Review intel.
15. Check `#gm-alerts` audit trail for one completed field battle and one completed siege.
16. Restart bot and confirm active state persists.
