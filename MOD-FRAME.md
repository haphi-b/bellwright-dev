# How to use "Mod Frame" for your mod

Mod Frame hosts other mods' screens inside the game's own windows, so a mod needs no HUD button or window of its own. It changes no game screen: it adds its parts while the game runs, so it doesn't conflict with mods that override these screens.

## The places
Mod Frame brings 3 new buttons in the vanilla screens: A 'more' tab in "inventory", a "…" tab in "settlements" and a "more" button on map. Each (except "more" in "inventory") only shown if a screen is registered to it.

Additionally you get a place to place "settings" and for really quick access you can register additional tabs to the "inventory" screen.

| Placement Key | Where  |
|---|---|
| `I` | the inventory (I), tab **More** (right end of the tab bar): a list on the left, your screen on the right |
| `I-tab` | a tab of its own in I's tab bar, after the game's tabs |
| `N` | the settlement window (N), the **…** icon tab: a list on the left, your screen on the right (the tab only shows while at least one screen is placed there) |
| `Map` | the world map: **⋘ more** at the top right opens a panel on the right side with a screen picker |
| `Settings` | the **Mod Settings** window (I → More → cog): a screen picker in its title, your screen below it over the whole width |

## Register a screen

1. Create a **Widget Blueprint** (parent class User Widget) anywhere under `/Game/Mods/<YourMod>/`.
2. That widget is automatically picked up by Mod Frame if you add a special **Tool Tip Text** for registration like:

   ```
   ModFrame|<your mod title>|<placement key>|<works only here>
   ```

   | Field | Values |
   |---|---|
   | `your mod title` | the name in lists, tabs, the map picker and Placement; optionally under a heading: `Heading/Title` or `Heading/N/Title` (see Headings) |
   | `placement key` | where the screen starts: `I`, `I-tab`, `N`, `Map` or `Settings` |
   | `works only here` | optional, not for `Settings`: a comma list of `I`, `I-tab`, `N`, `Map`. Gives the user a warning when changing the placement to that location|

   Examples:

   ```
   ModFrame|Free Storage|I
   ModFrame|Trade Ledger|N|N
   ModFrame|Weather|I-tab|I, I-tab
   ModFrame|Scout Reports|Map|Map
   ModFrame|Free Storage|Settings
   ```

That's all you actually need for registration by frame. No nodes no reference, just a tool tip. Without Mod Frame that widget is simply not created (unless you do it manually) so nothing breaks and your mod is still loaded but withouth the screen.

## Key Bindings
Mod Settings has a **Key bindings** screen (I → More → cog → General/Key bindings). It lists every mod action that was registered via **EnhancedInput**. They get grouped by mod, players can rebind them and it warns when two actions share a key. Your mod shows up there without any reference to Mod Frame. **It just needs to use Enhanced Input** with player-mappable keys and registers its mapping context. Without Mod Frame your hotkeys work the same, with their default keys.

### Create an EnhanceInput Hotkey

1. Create an asset of type **Input Action**
    - name it `IA_<YourMod>_<Action>` edit within it the following:
    - usually you'd use `Action / value type` "Digital (bool)"
    - Also set `Player Mappable Key Settings`:
        | | |
        |---|---|
        | `Name` | unique across all mods, e.g. `<YourMod>_<Action>`: it's the row's key in the player's key settings, so renaming it later loses the player's binding |
        | `Display Name` | the action's name in the list |
        | `Display Category` | your mod's name: the group heading (without it, Mod Frame uses your mod's folder name) |

2. Create an asset of type **Input Mapping Context** (maps the action to its default key):  
    - name it `IMC_<YourMod>`
    - `Default Key Mappings` should get one `Mappings` entry for all your **Input Actions**
    - So select an action and map it to a key here
    
3. **At start, in your logic actor**:  
 `Begin Play`: `Enable Input` (player controller 0); Get Enhanced Input Local Player
   Subsystem → `Add Mapping Context` (your context, priority 1); Get User Settings → `Register Input Mapping
   Context` (your context).
4. **Handle the action's event** (Started) in the logic actor, or in a component you add to the player's pawn. Not in a
   widget: a widget's own action event never fired in tests.

### What players get

- Possibly two keys per action (First and Second slot, mouse and keyboard), changed by clicking the key and pressing the new
  one. A dot marks a key that differs from its default.
- **Reset to defaults** per mod.
- **Same key as …** under an action whose key is used elsewhere: red for a game action (the game's action takes the
  key, yours never fires, and the game itself gives no warning), yellow for another mod's action, with its mod's name.
- Changes are saved at once by the engine (`Saved/SaveGames/EnhancedInputUserSettings.sav`). They survive restarts and
  the game's own "Reset to defaults", which resets only the game's actions.

### Rules

- **Read the key, don't keep a copy.** Don't save keys in your own SaveGame and map them again at start: that undoes
  every rebind made in Mod Frame at the next start or load. If your mod has its own key screen, read the current key
  from the player's key settings (Get User Settings → Get Active Key Profile → Find Key Mapping) and change it with
  Map Player Key + Save Settings. Set the Slot (`First`) in Map Player Key: left `Unspecified`, it adds a second row
  instead of changing the key.
- **For mods that do re-map at start:** players can tick **Overwrite mod's bindings** in that mod's "⋮" menu (off by
  default). Mod Frame then puts the mod's keys back after every change made elsewhere, and switches itself off for a
  mod that sets its keys back more than 10 times within 30 seconds.
- **Pick a default key the game doesn't use**, or your action never fires on it (e.g. L opens the Excursion factions,
  F9 is Quick Load). The game's key screen doesn't know mod actions, so only Mod Frame's screen shows such a clash.
- **Not covered:** gamepad keys and modifier combinations (Ctrl + key).
- **Co-op:** the logic actor runs on the host only. With your mod installed on host and client, hotkeys bound in the
  logic actor work for the host only; Mod Frame's screen also shows on the host only. Untested in co-op.

## Styling
### Headings

Several screens can be grouped under a heading: `ModFrame|My Mod/Overview|I` and `ModFrame|My Mod/Details|I`.

- `Heading/N/Title`: `N` is the order within the heading. Numbered entries come first, then the others alphabetically.
  Only the first two `/` split; a middle part that is no number is read as `Heading/Title` and logged.
- The lists in I and N show the heading as a row that opens and closes. The screen pickers of Mod Settings and
  the map show it as a line that can't be picked, its screens indented under it; the picker itself shows only the
  title.
- The numbers give the first order only; players can reorder in Placement.

## Design for the space you get

Mod Frame stretches your widget to fill its area both ways and clips what sticks out. Build it to stretch: **Fill**
sizes instead of fixed widths, a scroll box that takes the remaining height, a **Wrap Box** for rows of options that
may not fit.

| Placement | Area (width × height) | How known |
|---|---|---|
| `I-tab` | 852 × about 820 | measured in game: I's whole content area |
| `I` (I → More) | 606 × about 820 | I's content minus Mod Frame's list (852 − 246); measured in game |
| `N` (N → …) | 606 × about 820 | the same page in N's frame; measured in game |
| `Settings` | 852 × 824 | the "Mod Settings" window at I's place; its screens get the whole width (a screen picker in the title instead of the list); measured in game |
| `Map` | 606 × 878 | built as wide as the "More" areas (a 630 panel, 12 padding each side), from 100 below the map's top to 24 above its bottom; measured in game |

Sizes are in the game's UI units: pixels at 1080p. The game scales its UI by the screen's shorter side: scale =
shorter side ÷ 1080 (the Modkit's `Game/Config/DefaultEngine.ini`: `UIScaleRule=ShortestSide`, `DesignScreenSize`
1920×1080, a straight `UIScaleCurve` from 480 → 0.444 to 8640 → 8.0). So on any landscape screen the UI is
1080 units tall and 1080 × aspect ratio wide:

| Screen | Scale | Screen in UI units |
|---|---|---|
| 1920×1080 | 1.0 | 1920 × 1080 |
| 2560×1440 | 1.333 | 1920 × 1080 |
| 3440×1440 (21:9) | 1.333 | 2580 × 1080 |
| 1920×1200 (16:10) | 1.111 | 1728 × 1080 |

The areas above don't change with that: I's and N's frames have a fixed size in units (a wider screen only adds room
beside them; measured at 1920×1080 and 3440×1440), and the map panel has a fixed width at the right edge and the
screen's height, which is always 1080 units. The Settings and Map areas were measured with a widget showing its own
size in a 1835×1146 px window: scale 1.0611 = 1146 ÷ 1080, as the rule says. To preview a size, use the widget designer's screen size dropdown (top
right) → **Custom**.

## Details
### Check if your registration works
(...or the reason why it failed)

Open **I → More → cog (Mod Settings) → General/Log** after starting the game. The search writes:

- `searched <n> widget classes of the enabled mods`: all widgets found, registered or not;
- `registered '<title>' (<PLACEMENT>[, works only at …]) from <package>` for each registration;
- `ignored '<text>' (<package>): <reason>` for a registration that doesn't fit the format;
- `'<title>': unknown place '<value>' in works only here, ignored` for a wrong value in the fourth field;
- `<n> registrations` at the end.

**Write to game log** (same screen) also copies these lines into `%LOCALAPPDATA%\Bellwright\Saved\Logs\Bellwright.log`
(filter for `Mod Frame:`). It is off by default and applies from the next game start.

### Rules

- **Format:** case and spaces don't matter in the placement and works-only fields (`i-tab`, `I - Tab`, `map`).
- **Placement is only the start.** Any screen can go to any place, `Settings` registrations too. The player's choice
  is saved in `%LOCALAPPDATA%\Bellwright\Saved\SaveGames\ModFrame.sav`, and from then on it wins, also when an update
  of your mod changes the placement. A new registration goes to the end of its place.
- **The saved key is package path + title field.** If you rename or move the widget, or change its title field
  (heading included), players get it as a new registration at your placement, and their alias, order and place for
  the old one are dropped.
- **Works only here** never blocks a player. A place outside the list shows a ⚠ in Placement whose tooltip names
  your places. Use it when your screen needs a certain size.
- **One widget per screen.** A mod with a display screen and a settings screen registers two widgets, e.g.
  `…|I` and `…|Settings`.
- **The tooltip is the registration.** Mod Frame clears the Tool Tip Text of the widget it creates, so the
  registration doesn't show as a tooltip. Set tooltips on child widgets, not on the root.

### How Mod Frame finds a widget

Mod Frame searches once per game session, when a game starts or loads. It finds a widget when:

- its mod is **enabled** in the game's Mods menu;
- it is a **Widget Blueprint** whose class lies under `/Game/Mods/` and is **cooked into the mod**: listed in the
  mod's `assetsToCook` and `createdAssets` (the Modkit does that for widgets made in the mod's folder). Widget
  Blueprints with another parent than User Widget are untested.
- its **class default Tool Tip Text** starts with `ModFrame|` (case doesn't matter) and has at least the title and
  placement fields;
- the **title is not empty** and the **placement** is one of the five places.

A widget that fails one of the last two checks is logged as ignored. One that fails the first two isn't seen at all.

Set the Tool Tip Text in the widget designer. A value set from Python needs the field's reset arrow clicked once in
the designer, or a later compile may write the empty value back.

