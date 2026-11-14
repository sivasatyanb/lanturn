# Design: how Figma is used

**Figma file:** https://www.figma.com/design/1GlEqbaZG7xphZQwJ98Tic
(the owner must set link sharing to "anyone with the link can view" before submitting)

Planning, the agent evidence and the navigation preview are designed in Figma as variables, components and
six composed 390 x 844 screens. The app's colours (including the basemap), radii, spacing and type are
generated from that file, so the design and the product cannot drift apart silently.

| Figma | App |
|---|---|
| ![Plan](figma/01-plan.png) | ![App](screenshots/01-route-comparison.png) |
| ![Arrive by](figma/04-arrive-by.png) | ![App](screenshots/05-arrive-by.png) |
| ![Time picker](figma/05-time-picker.png) | ![App](screenshots/07-time-picker.png) |
| ![Navigation with call](figma/06-navigation-call.png) | ![App](screenshots/06-navigation.png) |
| ![Demo replay](figma/02-demo-replay.png) | ![App](screenshots/02-agent-update.png) |
| ![Evidence](figma/03-evidence.png) | ![App](screenshots/03-why-route-changed.png) |

## What is in the file

**Page "Route comparison flow"**: six screens built from component instances and editable vectors.

1. *01 Plan: compare routes*: Fastest and Better lit side by side, Leave at 22:30, Start above the cards.
2. *02 Demo mode: replay closure applied*: the same journey on the labelled replay data, rerouted around the
   closure. No banner: the status line carries the REPLAY tag and opens the evidence.
3. *03 Evidence: closure applied to routing*: the evidence sheet opened from the status line.
4. *04 Plan: Arrive by 00:30, across midnight*: date roll-over, leave times on the cards, and the hint that an
   earlier departure is a shorter walk.
5. *05 Time picker (24-hour)*: the sheet opened by Leave at and Arrive by.
6. *06 Navigation preview with companion call*: instruction at the top, call window below it at the upper
   right, fixed time, distance and ETA with Exit navigation at the lower right.

Screens 01 to 05 use the app's real route geometry for the 22:30 journey as editable vectors
(`scripts/export_design_map.py`); the map in 06 is a schematic of the start of Exhibition Road.

**Page "Components"**: 18 components, 9 of them variant sets, all bound to variables and text styles.

| Component | Variants / properties | Maps to in `web/` |
|---|---|---|
| Trip Header | text: From, To; nested When Control and time detail row | `#trip` |
| When Control | Mode (Now, Leave at, Arrive by) | `.when` |
| Time Picker | text: hour, minute, resolved date | `.tp` |
| Route Card | Route (Fastest, Better lit) x State (Default, Selected); text: Minutes, Times, Distance, Lit share, Unlit and unknown | `.route` |
| Start Button | | `.start` |
| Status Chip | text: Status (with a Replay tag in demo mode) | `.agent-chip` |
| Map Legend | exactly three entries: Lit, Unlit, Unknown (dashed) | `.legend` |
| Map Marker | Kind (Origin, Destination, Closure, Restriction) | `.mk`, `.mk-x` |
| Nav Instruction | text: Distance, Instruction, Then; turn arrow; call button | `.nav-top` |
| Nav Summary | text: Minutes, Distance and ETA; Exit navigation | `.nav-bottom` |
| Call Window | State (Connecting, Live, Failed) | `.call` |
| Decision Card | text: Record id, Location, Quote, Summary, Meta; nested Tag and Decision Pill | `.dec` |
| Tag | Kind (Replay, Synthetic, Live) | `.tag` |
| Decision Pill | Kind (Closed, Restricted, Neutral); text: Label | `.pill` |
| Button | Kind (Primary, Ghost); text: Label | `.btn` |
| Map Base | vector parks and main roads | basemap (illustrative) |
| Time Chip, Update Banner | marked "V1, retired": kept for reference, not used by any V2 screen | removed from the app |

**Variables**: one collection, "Lantern", mode "Night": 32 colours (9 of them for the map), 5 radii, 4 spacing
steps and the 44 px touch target, 42 in all. Every variable has explicit scopes and a WEB code syntax naming
the CSS custom property it becomes (`color/route/lit` is `var(--lit)`).

**Text styles**: 14 styles in Inter, from `Display/Nav` (30/34 extra bold) to `Body/Micro` (10.5/14).

## Design decisions made in Figma

- **Two cards, side by side, same anatomy.** Time is the largest thing on each card and the lighting bar sits
  directly under it, so the eye compares minutes first and lighting second without reading.
- **Three lighting states, three line styles.** Lit is amber, recorded-unlit is solid light grey, unknown is
  dashed grey, on the card bar, the legend and the route line. The two greys differ by pattern, not only by
  shade, so they still read apart over a dark road or a green park, and unknown can never be mistaken for lit.
- **The route colour identifies the route; the line style says what is known.** Fastest keeps its blue: a blue
  line when it is the alternative, a blue edge around the lighting colours when it is selected. Blue is not in
  the legend because it is an identity, not a lighting state.
- **A lighter night map.** The basemap went from near-black to a dark blue with visibly lighter roads, white
  labels with a dark halo and dark green parks, so a route across open ground is obvious. Routes sit under the
  street names so the names stay readable.
- **Planning is calm.** No explanatory paragraph under the cards and no alarm banner. A verified closure is
  applied to routing and reported in one status line that opens the evidence. The coloured closure marker and
  the Replay tag appear only in demo mode.
- **One prominent action.** Start is the only filled, full-width control on the planning screen and uses the
  Better lit amber, above the cards it acts on.
- **Time is a mode, then a value.** Leave now, Leave at and Arrive by are a three-way control; the chosen time
  and its date ("tomorrow, Sun 15 Nov") sit on their own line, and the picker shows the resolved date before
  the user commits.
- **In navigation, the instruction owns the top.** The call window sits below the instruction, at the upper
  right, so the manoeuvre is never covered. Hang up is the only control on the call and is always visible.
- **Evidence before explanation.** In the evidence card the quoted source sentence comes before the agent's
  summary, because the quote is the part the user can check.
- **Spacing on a 4-point scale and 44 px targets. Inter with tabular figures**, so minutes and percentages line
  up across the two cards.

## How the design reaches the code

```
Figma variables + text styles
   -> design/figma-tokens.json      (exported through the Figma connector)
   -> python scripts/build_tokens.py
   -> web/tokens.css                (generated; the only place colours, radii, spacing and type are defined)
   -> web/styles.css, web/app.js and web/mapstyle.js (use the tokens; map and route colours read them at run time)
```

To change the look: edit the variable in Figma, re-export `design/figma-tokens.json`, run
`python scripts/build_tokens.py`.

## How this was made, stated plainly

The Figma file was built during the hackathon by Claude Code through Figma's connector (variables, components
and screens are native, editable Figma layers, not pasted images). V1 was prototyped in code and then
systematised in Figma. For V2 the token changes (lighting greys, map colours, new type styles) were made in
code and Figma together and are identical in both; the new components and screens were then built in Figma
to match the working app. The team owns and can edit the file. If a judge asks "was it designed in Figma or
coded first", the honest answer is: prototyped in code, systematised and refined in Figma, with Figma the
source of truth for the visual tokens.
