# Active World Restoration

## Scope and decision status

This document inventories world-data changes in commit `41caf0e` relative to `cebe629`. The current checkout is merge commit `77726ed`, which merged `41caf0e`; `41caf0e` is the implementation commit, not the merge. It records the two approved restorations and the remaining decisions for disabled quests, unavailable resets, and inaccessible content.

Commit `41caf0e` activates 40 area files by adding 35 entries to `area/area.lst`; it also repairs VNUMs and syntax, and adds ferry, shipyard, and template rooms. Distinguish two historical findings: **AUD-092** is the observed fatal boot crash on duplicate room VNUM 1800 when both XFS Colony files load; **AUD-246** is the broader area-data finding that also catalogued duplicate room VNUMs 1800–1870 and other data anomalies. The implementation manifest includes lowercase `xfscolony.are` and omits `XFSCoolony.are`, so the immediate duplicate-load path is excluded from the manifest; the uppercase file remains on disk and was not reconciled or removed. Boot validation is needed to claim AUD-092 resolved.

## Approved and implemented first slice

### Riiice Service courier contract

The owner approved the courier contract. The implemented path uses the existing representative, package and Captain Harlef.

- **Offer / start:** existing Riiice Representative mobile VNUM `10248` in `area/corus.pt2` offers the delivery through existing Riiice job list object VNUM `10248` (`area/corus.pt2:497,764,774`). The list offers “Deliver the package to Captain Harlef” and tells the player how to accept the delivery. The list already includes courier and recovery jobs.
- **Required item:** existing object VNUM `10210`, named “package to captain harlef,” with description “This package is addressed to Captain Harlef” (`area/corus.pt2:356`). It is type 15 (`ITEM_TRASH`) with no special flags. Its identity directly supports this contract. Do not substitute the unrelated package VNUM `21209` mentioned by the disabled MC90 branch.
- **Turn-in target:** Captain Harlef, existing mobile VNUM `613` in active `area/darkhand` (`area/darkhand:248`). His historical program checked `ovnuminv(10210)` and attempted payment (`area/darkhand:264-271`), confirming recipient and item pairing.
- **Payout:** retain the target script’s original **5,000 credits and 100,000 experience** (`mpwithdraw 5000`, `give 5000 credits $n`, `mpgain $n 6 100000`). Do not also drop another 5,000-credit stack; player payment plus `drop` creates duplicate currency unless that stack is intentionally world loot.
- **Consumption / replay:** consume exactly one matching package only after successful validation, then award once. A failed/mismatched delivery consumes nothing and pays nothing. Make the contract repeatable only per package explicitly reissued by the Riiice Representative; the same object cannot pay twice. Do not issue packages from room resets. This avoids inventing persistent quest state while preventing replay with the same item.
- **Fallback:** if Harlef rejects the item, give a clear message; do not run `drop all`, move unrelated inventory, consume an item, or award currency/XP.

The representative issues package `10210` when a player accepts the contract. Delivery resolves before ordinary item transfer. The focused runtime check verified successful, invalid and repeated deliveries. The credits are fixed and leadership experience follows normal level caps. An issued package is bound to the player and is lost on death; the representative can then issue a replacement.

### Officer’s Lounge access

The owner approved restoration using a new room because the historical destination was absent.

- Room **126** is the Officer’s Lounge in `area/omni_complex`. Its north exit returns to Den’s Den.
- In room `101`, preserve the `speech_prog Gerard MacManus` and `name($n) == Den` condition, but transfer Den to room `126` instead of saying the lounge is unavailable (`area/omni_complex:150-153`).
- Give room `126` a clear return path to room `101` and a simple lounge description. Do not grant additional staff privileges or a shortcut to other restricted areas.

The focused runtime check verified Den’s admission, denial for the other fixture identity, and return to room 101. The active-world scanner found no duplicate room VNUM or broken room reference. VNUM `9493` has no room definition in `area/`; restoring it is unsupported. The lounge issue is AUD-246/WLD-041, distinct from the duplicate-file boot crash AUD-092.

## Remaining findings to approve

### Batch A — Restore safe, useful scripted service and turn-ins

Eight conditions were replaced by `if rand(0)` in five area files. Their previous conditions and surrounding commands are direct evidence of intended quest/service behavior, but not sufficient evidence to invent missing item prototypes or final story text.

| Area and current anchor | Previous condition and surrounding behavior | Design proposal | Original audit mapping |
|---|---|---|---|
| `area/Ryloth.are:116` | `ovnuminv(24611) == 1`; after Koh's'hak’s bounty dialogue, script gives a customer's follow-up dialogue. | Restore the exact item gate only after confirming object 24611 is loaded and identifying what follow-up means; if unavailable, remove this branch cleanly and retain the preceding bounty dialogue. Do not invent a reward. | AUD-246, source WLD-041; AUD-174 world-data coverage |
| `area/Ryloth.are:419` | `ovnuminv(2100) >= 1`; merchant reacts to the ale, withdraws 5,000, gives 5,000 credits, drops 5,000 credits. | Make explicit whether this is an ale sale, fee, or exploit-prone payout. Proposed safe design: consume one eligible ale only after validating it, pay one fixed amount once, and give clear insufficient-item feedback. Price and repeatability need approval. | AUD-246 / WLD-041 |
| `area/corus.pt2:343` | `ovnuminv(24611) >= 1`; Locke thanks the player, withdraws 5,000, transfers 5,000 credits, and drops credits. | Treat as the same bounty-token turn-in family as the Ryloth 24611 branch only if the narrative owner confirms they share an objective. Choose one canonical NPC/turn-in and prevent duplicate rewards across zones; otherwise author separate quest state. | AUD-246 / WLD-041 |
| `area/corus.pt2:528` | `give_prog diskstolen`; `ovnuminv(10598) >= 1`; thanks player, withdraws 3,000, gives and drops credits. | Restore as an explicit stolen-disk delivery. Confirm disk identity/ownership, whether the item is consumed, and one-time completion state. Proposed reward is the existing 3,000 credits, pending balance approval. | AUD-246 / WLD-041 |
| `area/hunters.guild:146` | `ovnuminv(11113) >= 1`; hunter-target turn-in dialogue and bounty reward logic follows. | Restore a target-specific bounty hand-in only after confirming item 11113 exists and is created by a supported source. Add one-time completion/consumption rule; preserve existing numeric payout only after review. | AUD-246 / WLD-041 |
| `area/hunters.guild:222` | `ovnuminv(3202) >= 1`; parallel bounty turn-in dialogue and reward logic. | Same safety conditions as the prior guild branch; approve separately whether this is a distinct target, shared bounty-token system, or retired mission. | AUD-246 / WLD-041 |
| `area/kuatcity.are:288` | `ovnuminv(24611) == 1`; royal NPC asks the player to obtain an item, with the old success branch below. | Confirm whether this shares the 24611 bounty/royal objective above. If shared, make a single completion and reward path. If separate, assign a distinct quest item or durable quest state; do not reward possession alone repeatedly. | AUD-246 / WLD-041 |
| `area/mc90:89` | `ovnuminv(21209) >= 1`; package delivery dialogue, followed by 2,500-credit withdrawal/payment. | Restore as a delivery with explicit recipient, item consumption, and a one-time completion rule. Approve the existing 2,500-credit amount or replace it with a chosen amount. | AUD-246 / WLD-041 |

**Cross-cutting approvals for Batch A:** whether old item checks mean inventory possession or a `give_prog` transfer; which item is consumed; whether a player can complete a mission again; payout amount and currency creation method; and whether bounty 24611 denotes one shared mission or multiple distinct stories. Existing scripts do not establish a durable quest-state mechanism. Prefer an item-to-NPC delivery trigger or explicit quest state to a repeatedly callable possession check. Add invalid-item/duplicate-completion messages as authored behavior. Do not use `mpwithdraw` plus both `give` and `drop` until the recipient and intended currency flow are clear.

### Batch B — Restore access paths and named interactions

| Current anchor | Merged fallback | Proposal | Evidence / audit mapping |
|---|---|---|---|
| `area/omni_complex:152` | The Gerard MacManus branch says “The Officer's Lounge is unavailable.” instead of transferring to room 9493. | See the ready-for-approval proposal above: create room 126, link a return path, and retain Den-only access, subject to owner approval and collision validation. | AUD-246 / WLD-041 identified the old invalid transfer VNUM 9493. |
| `area/omni_complex:145-146` (historical) | Former `mpoload 21016` referenced a missing prototype; replacement behavior is in the adjacent area data diff. | Do not restore the missing item by guess. If the dispenser/meal interaction is desired, use an existing ration prototype and state whether it is free or gated. | AUD-246 / WLD-041. |
| `area/omni_complex:513-516` (historical) | Former `mpat gilito mpoload 22501` and glitterstim drop referenced missing item 22501. Current path is disabled with `break`. | Leave disabled until the item prototype and gameplay purpose are approved, or substitute an existing item only after design owner approves the changed fiction and economy. | AUD-246 / WLD-041. |
| `area/limbo.are:576-577` (historical) | Autopilot script used `mpat 5991 autopilot`, an invalid room target. Current code breaks. | Preserve disabled state until a real target room/command path is identified; repair requires a valid room VNUM and confirmation that `autopilot` is a supported command for the scripted actor. | AUD-246 / WLD-041. |
| `area/limbo.are:595-605` (historical) | Script used item 10208 and `mpat 10299` in Coruscant content. | The newly active Coruscant area may make this path viable, but confirm the specific room and actor target; use a direct valid target rather than retaining an assumed old VNUM. | AUD-246 / WLD-041. |
| `area/limbo.are:1472-1473` (historical) | Room script `mpmload 329` depended on an unloaded Coruscant area. | With `coruscant_streets` active, confirm mob 329 exists there and is intended to spawn in this context; otherwise retire the trigger. | AUD-246 / WLD-041. |

The merged data also adds Coruscant ferry rooms `199/200` in `area/coruscant_streets:1583` and Kuat ferry rooms `10197/10198` in `area/corus.pt2:791`. These are concrete, documented recovery anchors for `AUD-174`/`SPC-030`: ferry ship files reference those cockpit/entrance rooms. Keep both paired room links; do not repurpose these VNUMs without updating and validating their ship files.

### Batch C — Reset omissions: restore prototypes or approve permanent retirement

The merge comments out **33 reset commands** as `* Unavailable prototype:`: 10 mobile (`M`), 12 give (`G`), 10 object (`O`), and 1 equip (`E`). A comment makes the intent visible but also means the reset no longer executes. There is also one commented duplicate wield reset in `area/space.are:9773` (`E 1 53 1 16`); its paired earlier reset remains. Do not include that duplicate in the 33 unavailable-prototype count.

| Area / current lines | Omitted reset targets | Decision proposal |
|---|---|---|
| `area/corulag.are:4279,4285` | mob 10104; object 28207 | Confirm intended guards/gear. Restore only if valid prototypes and the encounter remains in balance. |
| `area/honoghr:1620` | object 28100 | Verify prototype identity and slot; restore if it is the intended equipment. |
| `area/hunter.ships:1809-1811` | mob 204, three instances | Determine whether 3 repetitions of generic mob 204 were intentional ship crew. If yes, restore after mob-role review; otherwise leave retired. |
| `area/hunters.guild:1338-1341,1355,1367,1370-1375` | mob 204 (4); objects 5652, 2205, 2213; gives 2222, 9551, 2218, 2214, 2220 | Audit the guild encounter and each item's role together; the reset cluster contains 13 unavailable commands across multiple rooms. Do not replace with superficially similar items. |
| `area/kashyyyk:1746` | object 31522 | Verify the intended guild/quest equipment, then restore or retire. |
| `area/mall:1764-1767,1771` | mob 204 (2); objects 21203, 1454 | Decide whether the two generic NPCs and sale/fixture objects are intended; restore as a coherent mall scene only after confirming objects. |
| `area/mosespa.are:1733,1741,1759` | objects 28207, 31506, 24600 | Identify each item and intended NPC loadout; restore together if they form one valid set. |
| `area/quarren.are:5494,5585-5597` | object 31522; objects 21115 and 21116 in three rooms | Review mine/Quarren loadouts; the six room placements may be repeated fixtures. Restore with explicit count and reset-room intent. |

The canonical source IDs are **AUD-246** (area anomalies, broken exits/resets, duplicate VNUMs) and **AUD-174** (unloaded world/ship baseline). The old baseline scanner listed these reset references as broken because the referenced prototypes were absent from loaded areas. In the merged data, most remain unavailable after activation. Before creating replacement prototypes, compare every candidate VNUM against all active and inactive area object/mobile sections. Never create one of these VNUMs if it collides with an existing definition; do not infer names or stats from numeric IDs.

### Batch D — Decide the three explicit replacement objects

| New anchor | Added prototype | Evidence-backed behavior | Approval needed |
|---|---|---|---|
| `area/limbo.are:1153` | Object 61, generic furniture (`ITEM_FURNITURE`, flags 0, level 10) | Fixes the exact missing result VNUM used by legacy chair/couch recipes and is explicitly cited in AUD-021. | Approve the generic description and whether both recipes should create the same object. Production use needs recipe behavior and inventory/output review. |
| `area/newacad.are:793` | Object 10432, “prototype forcepike” (`ITEM_WEAPON`) | Adds a concrete forcepike prototype. | No listed disabled reset directly points to 10432 in this diff; identify intended acquisition/quest path before adding one. Confirm weapon type/flags/values and progression balance. |
| `area/newacad.are:801` | Object 10433, “prototype disguise” (`ITEM_ARMOR` per type 92) | Adds a concrete disguise prototype. | No listed disabled reset directly points to 10433; confirm its actual disguise effect and expected use. Type/description alone do not establish function. |

These are three objects; there is no replacement mobile in this batch. They do not justify substituting for unavailable reset IDs without proof of intended identity. AUD-021 is the direct provenance for VNUM 61; 10432/10433 are additions visible in the merge but have no strong mapping to a missing reset in this inventory.

## Evidence-backed corrections versus design choices

The following are data correctness repairs that can be implemented once their anchors are validated: malformed MUDProg headers (e.g. `corellia.are:504` `hitprcnt_prog 20`, which corrects old `hitprcnt_prog prog 20`; `honoghr:34` fight percent normalized to 100; empty headers made numeric zero), replacing invalid `exit_key` values with `-1`, redirecting invalid scripted room calls to an extant documented room where semantically safe, activating missing world files, and removing a demonstrable duplicate reset. They should still receive parser/boot and world-state verification.

The following need design approval first: restoring any reward-bearing quest path; choosing whether bounty token 24611 is one mission or several; item consumption and repeatability; the 33 omitted reset populations/equipment; the Officer's Lounge destination/access; whether room removals are intended content retirement or broken links; combat/economy balance; and replacing unavailable prototypes with new content. No source evidence specifies these design facts.

The merge also changes descriptive text/flags in individual areas and removes exits from `grand_towers`, `hunters.guild`, `mosespa.are`, and `quarren.are`. Treat these as world-layout choices for review, not automatically as safe technical cleanup. Their exact diff anchors are available with `git diff --unified=2 cebe629..41caf0e -- area`.

## Approval sequence and implementation boundaries

1. Approve Batch A quest/service semantics and payout/item policies.
2. Approve Batch B valid access paths and whether unavailable interactions remain retired.
3. Approve Batch C per reset cluster (restore valid existing prototypes, author new prototypes with explicit specs, or permanently retire).
4. Approve Batch D prototype identity and intended usage.
5. Only then implement game-data replacements. Keep original VNUM identity where valid; allocate new VNUMs only after a full collision and reference audit. Preserve VNUMs already persisted in characters, ships, quests, and save data. Where an object may already be carried in a saved player inventory, update its prototype compatibly rather than changing its type/meaning in place.

Suggested validation after implementation: parse/boot all active areas; assert no duplicate VNUMs, invalid reset targets, unresolved exit keys, or malformed program headers; verify all area MUDProg targets exist and referenced commands/triggers are supported; and test every approved quest path for missing/incorrect items, successful one-time completion, repeat attempts, payout, and save/reload. These checks validate implementation; they do not replace design approval.
