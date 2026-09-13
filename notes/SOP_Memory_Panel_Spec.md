# SOP Memory Panel — build specification

A diagnostic Python Panel (Qt widgets) for Houdini that profiles the memory of a cooked
SOP's output geometry. Everything displayed comes from one provider call; this document
describes what to draw, where, and how it responds. It does not assume any existing Python.

**Provider:** `_page_tools` (built from `src/_page_tools.C`). Entry point:
`report(node_path, output_index=<view output>)` → plain dict. Read the schema block at the
top of that source file first; every key referenced below is defined there.

**Panel mechanics:** follow Houdini's shipped Python Panel examples for the interface XML
and `onCreateInterface` / `onNodePathChanged` / `onDestroyInterface`.

The panel never modifies geometry and never forces a cook.

---

## 1. Layout

Two stacked sections in a draggable vertical splitter, with a title bar above them.

```
┌────────────────────────────────────────────────────────────────────┐
│ /obj/geo1/blast1                    [Output ▾]  [Pin]  [❚❚ Pause]  │  title bar
│ ⚠ Instanced — the output is fully shared with another detail…      │  (only when true)
├────────────────────────────────────────────────────────────────────┤
│ ▼ Memory                                                           │
│   Scopes: [■Attribute Set ■Public ■Private ■Groups]                │
│           ■Primitive Lists  ■Index Maps  ■Group Tables             │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ Component        %      Total    New   Unique  ID  Pages  Type│  │
│  │ ▼ Geometry Memory ███ 12.8 MB  4.1 MB  12.8 MB                │  │
│  │   ▼ Attribute Set   ██ 11.9 MB …                              │  │
│  │     ▼ Point       ██   8.2 MB …                               │  │
│  │         P         ██   4.1 MB …        12  0/4/0  numeric[3]  │  │
│  │         Cd        █    2.1 MB …        17  0/4/0  numeric[3]  │  │
│  │     ▶ Primitive   █    3.7 MB …                               │  │
│  │     Data Structure Overhead  ▏  1.2 KB …                      │  │
│  │   ▶ Primitive List ██  2.1 MB …                               │  │
│  │   ▶ Index Maps    ▏  32 KB …                                  │  │
│  │   ▶ Group Tables  ▏  16 KB …                                  │  │
│  │   Detail Object   ▏   6.5 KB …                                │  │
│  │   Unaccounted     ▏    0 B …                                  │  │
│  └──────────────────────────────────────────────────────────────┘  │
├════════════════════════════ (drag) ════════════════════════════════┤
│ ▼ Index Map Pages                                                  │
│   Owner [Point ▾]  Mode [Occupancy ▾]  Cell [3px ▾]  □Legend       │
│   ■ active  ■ vacant  ■ temporary  ■ out-of-range                  │  (legend, optional)
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  ▦▦▦▦   ▦▦▦▦   ▦▦▦▦   ▦▦▦▦   ▦▦▦▦   ▦▦▦▦        page cards   │  │
│  │  ▬▬▬▬   ▬▬▬▬   ▬▬▬▬   ▬▬▬▬   ▬▬▬▬   ▬▬▬▬        attr bars    │  │
│  │  ▬▬▬▬   ▬▬▬▬   ▬▬▬▬   ▬▬▬▬   ▬▬▬▬   ▬▬▬▬                     │  │
│  │  ▦▦▦▦   ▦▦▦▦   ▦▦▦▦   ▦▦▦▦   ▦▦▦▦   ▦▦▦▦                     │  │
│  └──────────────────────────────────────────────────────────────┘  │
│   point page 37   active 1024   temporary 0   vacant 0             │  status line
└────────────────────────────────────────────────────────────────────┘
```

**Sizing:** panel margin 4 px. Splitter starts at roughly 260 px top / 520 px bottom,
remembers user drags. Neither section collapses to nothing by dragging. Collapsing via the
header gives all space to the other section; re-opening restores the remembered split.

**Section headers:** bold 13 pt with a disclosure triangle. Panel title: bold 17 pt.

---

## 2. Title bar

| Control | Behaviour |
|---|---|
| **Node path** (left) | The SOP's path. "No SOP node selected" when empty. On error: path + error message. |
| **Output selector** | Dropdown of output names. Hidden for single-output nodes. Resets on node change. Choosing one re-reports that output. |
| **Pin** | Toggle (`Pin` / `Pinned`). Captures the current report as the A side and enters compare mode (§5). Disabled with no report. Bold while held. Placed left of Reload. Does not change pause state. |
| **Reload** | Shown only while paused. Adopts the node selected since pause, reports it, stays paused. No outstanding selection: re-reports current node. |
| **Pause** | Toggle. Stops following selection, parameters, and timeline. Un-pausing applies whatever arrived while frozen. Label and icon stay fixed; only colour changes, so the button doesn't jump. |
| **Instanced warning** | Amber line under the title, shown only when the report says the output is another node's detail. Wording: this node allocated nothing; the total belongs to the shared geometry. |

**Pause button styling:** colour it as a ticked Houdini checkbox — `checkedBase` fill,
`checkedFg` mark, border blended 25% from fill toward mark. Bold label. Applied only while
the toggle is on; cleared when off, so the resting button carries no rule of ours.

> **Trap:** Under H22's new UI the resting button may look inverted. This is a seam in
> Houdini's own theming (new-UI chrome around a legacy-painted button) — measured, and
> removing our rule did not change it. Shipped panels share the same appearance.

---

## 3. Memory tree

A single tree rooted at **Geometry Memory** (whole-geometry totals). Six fixed children,
always in this order, mirroring `GA_Detail::countMemory()`:

| Row | Contains |
|---|---|
| **Attribute Set** | One sub-row per owner (Point, Primitive, Vertex, Detail), each listing its attributes and groups. A **Data Structure Overhead** row follows last: the attribute container's own bookkeeping bytes. |
| **Primitive List** | Always has at least one child (see below). |
| **Index Maps** | One sub-row per owner (Point, Primitive, Vertex, Detail), one per `GA_Index_Map`. The Type column carries a trivial/monotonic/non-monotonic classification. |
| **Group Tables** | Point / Primitive / Vertex / Edge sub-rows. |
| **Detail Object** | `sizeof(GU_Detail)`. Always present unconditionally. |
| **Unaccounted** | What the provider could not attribute. |

Every branch row is always shown, at every scope-toggle state. A scope toggle hides only
that branch's children, never the branch row itself.

**Primitive List** takes one of two forms. When the geometry has per-primitive objects (full
representation), one child row per primitive type shows name, count, and share, followed
last by a **Data Structure Overhead** row; these children sum exactly to the Primitive List
total. Primitive List's root row **starts collapsed** in this form, hiding the per-type
rows until expanded, and carries the list's data id and representation label
(`full primitive list` / `paged primitive list`), because no child names the list as a
whole. When the geometry stores a compact (paged) list, the single child is **Paged Vertex
List**, which carries the data id and the page-detail checkbox/bar toggle.

**Index Maps** sub-rows are never individually hidden, even at 0 bytes: the Type column
classification is informative regardless of byte size.

**Group Tables** sub-rows each individually disappear when their own total is 0 bytes — a
group table's row is a pure byte count with no other classification to preserve. The Edge
sub-row's children are: one row per edge group (e.g. `edge_0`), followed by a **Data
Structure Overhead** row (the table's name-map cost), summing to the Edge row's total.

**Detail Object** carries no toggle: a `GU_Detail` cannot be 0 bytes.

### Columns

`Component | % | Total Memory | New Memory | Unique Memory | Data ID | Pages c/s/h | Type`

| Column | Detail |
|---|---|
| **%** | Horizontal bar sized against the geometry total, with percentage text over it. Minimum width ~72 px. Colour: theme slider roles — groove in `field`, fill in `primary`. Draw text twice, clipped to each half, in that half's foreground (`primaryFg` / `fieldFg`). |
| **Total / New / Unique** | Human-readable sizes ("4.1 MB"). Sort by raw byte count, never the formatted string. |
| **Total sharing suffix** | When the provider reports intra-detail sharing: `4.1 MB (4.0 MB shared)`. Omit entirely when none. Display only — must not affect sort key. |
| **Data ID** | Raw id, or "-" if unset. *Italic* when also found on an input. Tooltip: describes values, not storage. |
| **Pages c/s/h** | Three slots: `12/3/1` (measured), `12/-/-` (split unmeasurable), `-` (no page data at all). Dashes mean unknown, never zero. |
| **Type** | Type with tuple size (`numeric[3]`). Append `(tail-init)` when flagged. Primitive list row: `paged primitive list` or `full primitive list`. Index map rows: `trivial`, `monotonic`, or `non-monotonic`. |

**Column widths** are fit to content once, the first time the tree has data. A later
rebuild — scope-filter toggle, node switch, un-pausing — preserves whatever width the user
has set by dragging.

### Row rules

* The root **Geometry Memory** row is always bold. Among its descendants, a row is bold only
  when it has a colour-bearing scope (an entry in §7's scope table) and non-zero New. A
  container or bookkeeping row (Attribute Set, Index Maps, Group Tables, Detail Object,
  Data Structure Overhead, Unaccounted) is never bold regardless of its New value.
* **Text colour** is the row's scope (§7). Structural rows use default text colour.
* Rows sort within their group on header click. Owner rows and branch rows keep fixed
  order. **Pages is not sortable.**

### Scope filters

Seven checkboxes above the tree, in fixed order, all on by default:

| Checkbox | Gates |
|---|---|
| **Attribute Set** | Attribute Set's owner rows and its Data Structure Overhead row. |
| **Public** | Public-scope attribute leaves within Attribute Set's owner rows. |
| **Private** | Private-scope attribute leaves within Attribute Set's owner rows. |
| **Groups** | Element-group leaves within Attribute Set's owner rows. Does **not** affect edge groups. |
| **Primitive Lists** | Primitive List's children (per-type rows and Data Structure Overhead, or Paged Vertex List). |
| **Index Maps** | Index Maps' per-owner children. |
| **Group Tables** | Group Tables' Point/Primitive/Vertex/Edge children (edge groups included). |

Attribute Set, Public, Private, and Groups are visually boxed together (a plain native Qt
frame — no colour of its own, so it follows the theme). When Attribute Set is unchecked,
Public, Private, and Groups are hidden — not merely left checked-but-inert — since they
would have nothing left to filter. Public, Private, and Groups reappear when Attribute Set
is checked again.

Checkboxes whose scope has a colour in §7 carry a scope-coloured label; the rest (Attribute
Set, Index Maps, Group Tables) carry a plain text label.

Filters hide rows only. **They must never change a total.** An owner row can legitimately
show more than the sum of its visible children.

> **Trap:** Deriving totals from visible rows is the classic bug — hidden bytes vanish and
> reappear in Unaccounted.

### Sharing peer highlight

Selecting an attribute row **shades the rows it shares with** — a soft wash on the row
background using the theme's accent (`pluto_primary`, not `highlight`). Start at the item
rect, not the viewport edge. Peers hidden by scope or under another owner are skipped.

---

## 4. Page grid

Geometry stores elements in fixed pages of 1024 slots. One **card** per page, wrapped into
columns, scrolling vertically.

Each card is a **32×32 block of cells** at the chosen zoom (1–10 px per cell; default 3, so
96×96 px). Cards: 10 px horizontal gap, 14 px vertical, 2 px left margin. Cell separators:
thin at 4 px+, 2 px at 8 px+.

**Controls:** Owner dropdown (Point / Primitive / Vertex / Detail), Mode dropdown, Cell size
dropdown (1, 2, 3, 4, 6, 8, 10 px), Legend checkbox.

### Modes

#### Occupancy

Colour each slot by status: active (live element), vacant (hole), transient, or
out-of-range (past end of last page, near-black).

#### Continuous block

Colour each run of consecutive live elements a different soft hue; everything else neutral
gray. Shows how badly element ordering has been fragmented.

#### Memory block sharing

For the **attribute selected in the tree**, colour each page by which memory block it sits
on. Two pages of one colour are one allocation, whether within this attribute or across two
attributes.

**Block id labels.** Each tile carries its block id in **base 36**. Base 36 because block
ids are arbitrary identifiers, not ordinals — decimal invites false inferences about
adjacency; letters read as identifiers. Use the same base on the status line.

Label rules:
- Font: Houdini's UI font (Lato, `$HFS/houdini/fonts`).
- Size: fixed to the widest id in the report, so labels don't vary tile to tile.
- Advance: lay glyphs on their real advances, not a fixed cell.
- **Block 0 is never labelled** — it means "no other page reaches this block."
- Tiles too small for a readable label get none.
- **No cell separators** in this mode — the block is a property of the whole page.

**Ink colour.** Chosen per block from that block's own tile lightness — the neutral (dark or
light) that separates further in Oklab L. When a block is pinned, labels on other tiles
blend toward the canvas by the same amount their tiles do.

**Click to highlight.** Clicking a page highlights every other page on its block by dimming
the rest (whole card including bars). The highlight is detail-wide: it survives selecting a
different attribute. Clicking the same block again, or a block nothing else reaches, clears
it. A **pinned block's tiles get a thin ring** in `pluto_primary`.

> **Why click, not hover:** highlighting repaints the whole grid, so following the cursor
> makes the mode restless. Hover fills the status line only.

**Selection-driven, not checkbox-driven.** When nothing is selected: "Select an attribute."
When the selected attribute has no page data or shares nothing: say which — the cases are
different and a generic "nothing to show" is ambiguous.

> **Trap:** Every empty-state message must be guarded by its specific condition. An
> unconditional `else` turns the message into a claim about whatever case was missed — e.g.,
> a numeric attribute sharing only constant pages fell through to the dict/string wording.

**Constant pages are drawn like any other.** The provider resolves whether a constant page
is shared by matching its pointer against the blocks `countMemory()` reported. Grey means
"measured as not shared", not "could not be determined."

**Self-sharing.** An attribute sharing a block with another of its own pages enters this
mode with no bars (that relation is in the tiles). An attribute with no siblings but with
self-sharing still has something to show.

**Page-count change while selected.** When a live re-cook changes the page count while this
mode has an attribute selected, the panel must drop any cached sharing data whose length no
longer matches the current page count, rather than drawing from it. The selected attribute
itself stays selected; one of this section's other empty-state messages applies until the
data is repopulated. The normal selection-refresh flow repopulates it from the current
selection immediately, so this case is not normally visible.

**Dict and string attributes** will read as sharing nothing even when they share megabytes,
because their pages hold handles into a value table and a copy allocates fresh handles while
sharing the table. Say so on the status line and point at the Total column.

### Attribute bars

Under each card, one thin horizontal bar per toggled attribute — same width as the card,
height = cell size (minimum 4 px), 4 px below the card, 6 px apart.

**Normal modes:** each bar colours that page by storage state: constant, shared, hardened,
constant-and-shared, or unknown.

**Constant-and-shared is its own colour** — a constant page whose value is wide enough to
need a heap allocation gets refcounted and can be shared. Colouring it purely "constant" or
purely "shared" misrepresents it.

**Memory block sharing mode:** bars mean one row per *peer* of the selected attribute,
filled only on pages whose block that peer shares, drawn in **that peer's own colour**.

Bar colour rules:
- Colours are **fixed detail-wide**, assigned from a stable order over every attribute in the
  report. A per-selection rebuild would make the same attribute two colours on different
  cards.
- **Peers on another owner are drawn like any other.** A primitive attribute can light a bar
  under a point page. Bars carry their own owner for tree-row resolution.
- **Bar order matches the tree.** The Nth bar is the Nth toggled row as it currently appears.
  Take the order from the tree, not a separate sort.

Attributes get a bar checkbox only if they have page data. Rows without one get a blank
spacer (for alignment), unless they have children (the disclosure triangle occupies that
slot).

### Generated palettes

Three things need distinct colours: continuous-block runs, memory blocks, and per-attribute
bar colours. All come from one generator under three seeds.

Requirements:
1. **Index *i* keeps its colour regardless of how many colours were requested.** Use the
   **van der Corput sequence** (bit-reversal), offset by a per-seed start.
2. **Hues placed on a constant-lightness, constant-chroma Oklch ring**, so equal angular
   steps are perceptually equal. Chroma chosen to be inside sRGB at every hue (no gamut
   mapping needed).

> The panel does the Oklch → sRGB conversion itself in numpy (paint-path performance). The
> test suite checks against `coloraide` so the shortcut stays honest.

### Legend

A row of 14 px swatches with labels, hidden unless the checkbox is on. Shows the current
mode's colours plus storage colours when any bar is visible.

- "unknown" swatch: shown only when something on screen has unknown pages.
- "constant, shared" swatch: conditional (narrow label; omit when absent).
- In Memory block sharing mode, tiles and bars share one entry: "unique colour per memory
  block / attribute". The "/ attribute" part is conditional on bars being drawn.

**Legend labels set a hard floor on the panel's width** (the row does not wrap). Keep each
label to the shortest phrase that stands alone.

### Status line

Shows what is under the cursor:

| Context | Format |
|---|---|
| Over a card (Occupancy) | `point page 37 [37888 - 38911]   active 1024   temporary 0   vacant 0` |
| Over a bar | `P [public]   page 37 [37888 - 38911]   shared` |
| Over a bar (sharing mode) | `id / id_copy [point public]   page 37 [37888 - 38911]   shares this page's block` |
| Over a card (sharing mode) | `point page 37 [37888 - 38911]   block 3, also on pages 8, 16` |

Rules:
- Every line that names a page names the **offsets** it covers (not element numbers — those
  diverge on a fragmented map). The last page's range stops at the map's end.
- In Memory block sharing mode, omit occupancy counts — the card answers "which block", and
  occupancy crowds that out.
- A page whose block nothing else reaches says so, rather than falling silent.
- Truncate long page lists with a count.
- Empty grid: "No pages (empty geometry or no node selected)", centred, light gray.

---

## 5. Compare mode

Pin a node's report, then read whatever is selected against it. Comparison is the memory
tree with delta columns; the page grid takes no part.

### Entering and exiting

Pin captures the current report as the **A side**. The captured report is fixed — not
re-read, does not follow its node, remains valid after node deletion. Pin and Pause are
independent in both directions.

| State | Live side |
|---|---|
| pinned, not paused | follows selection |
| pinned, paused | changes only on Reload or un-pause |

### What the body shows

| Condition | Body |
|---|---|
| pinned, no node selected | the pinned node's report in §3 columns |
| pinned, node selected | the delta tree |
| pinned, node selected, no row differs | `No differences.` |

Deselecting shows the pinned report; re-selecting any node (including the pinned one)
returns to the delta tree. The A side does not follow the playbar, so holding the pinned
node selected and changing frame compares frame to frame.

### Layout while pinned

The Index Map Pages section is hidden in every compare state. Un-pinning restores it at the
splitter sizes stored before the mode was entered. Bar checkboxes are hidden with it.

### Title bar

```
/obj/geo1/grid1  →  /obj/geo1/blast1     [Output ▾]  [Pinned]  [⟳ Reload]  [❚❚ Paused]
```

With no live node: `/obj/geo1/grid1   (pinned)`.

A **pending-selection line** appears while paused with an unapplied selection:
`blast1 selected — Reload or un-pause to read it.`

The **instanced warning identifies which side is instanced** and its effect on Δ New / Δ
Unique (see below).

### Delta tree columns

```
Component | Scope | Δ Total | Δ New | Δ Unique | Δ Pages | Δ c/s/h | Data ID | Type
```

| Column | Detail |
|---|---|
| **Δ Total / Δ New / Δ Unique** | B − A. Tooltip on Δ Total shows the A and B absolutes. |
| **Δ Pages** | Change in `num_pages`. Not the sum of Δ c/s/h. Carried on owner rows and their index maps, repeated on each attribute. |
| **Every delta** | Explicit `+` or `−`. Measured zero: `0`, dimmed. |
| **Δ c/s/h and Data ID** | Not sortable. Data ID shows `A → B` on retained rows. |

### Row visibility

Retain a row when it differs, plus every ancestor of a retained row (an ancestor is retained
even when its own delta is zero). A difference in `data_id` alone does not retain a row.

Structural rows keep their canonical order under any sort.

### Unknown handling

The dash rule from §3 applies unchanged. **Unknown on either side makes the slot unknown.**

| State | Single node | Diff |
|---|---|---|
| no `page_details` (array, blob, index-pair) | `-` | `-` in both Δ c/s/h and Δ Pages |
| `has_hardened_details` false (element groups, paged prim list) | `2/-/-` | `Δ/-/-` |
| present on one side only (added/removed) | — | signed full counts (measured, not unknown) |

A `has_hardened_details` that differs between A and B leaves the affected slots unknown.

**An instanced side empties Δ New and Δ Unique on every row** — the provider reports zero
for both on an instanced detail, so no delta from them is defined. Δ Total is unaffected.

### Row state and colour

Row states are carried by **font, not colour**, applied to every column:

| Font | Meaning |
|---|---|
| **bold** | added — present on B side only |
| ~~strikethrough~~ | removed — present on A side only |
| *italic* | replaced — same key, different attribute type |

A replaced row's Type reads `numeric[1] → string[1]`. Its Δ c/s/h carries measured counts
(the dash is reserved for "not measurable").

These rows take **no row background** — row backgrounds are not a surface this panel paints.
Growth and shrink take their own hue pair (distinct from scope and storage palettes). Unknown
takes neutral grey.

This mode has **no legend** — every colour is accompanied by a non-colour channel (sign,
dash, `0`, Scope column, or `A → B`).

---

## 6. Interaction

| Action | Result |
|---|---|
| Select a SOP | Re-report and redraw (unless paused) |
| Node cooks / parameters change / timeline moves | Same |
| Tick an attribute checkbox | Adds its bar under every card |
| Click an attribute row | Its bar is emphasised; all other bars dim toward the background |
| Click a row whose bar isn't shown | Status line: "toggle *name* on to see its bar" (or "switch Owner to *X*" for a different owner) |
| Hover a page card | Status line shows page info. Never repaints the grid |
| Click a page card | Sharing mode only: highlights that block. Click again to clear |
| Hover an attribute bar | Status line shows attribute + storage; matching tree row gets a coloured border |
| Click a column header | Sorts attribute rows within their owner group |
| Toggle a scope | Hides/shows rows and bars; totals unchanged |
| Collapse a section | Other section takes the space; re-opening restores split |
| Change Owner / Mode / Cell | Redraws the grid |
| Press Pause | Freezes everything until un-paused |

**Two separate highlights:** clicking a row *selects* (persistent background + bar emphasis).
Hovering a bar *outlines* the matching row (border). Hovering must never disturb the
selection.

### State persistence across nodes

Rebuilding the tree (different SOP, re-cook, un-pause) must not discard where the user was
looking. Three pieces of state survive, restored by row identity:

| State | Remembered for |
|---|---|
| bar toggles | attribute rows |
| selected row | any row |
| opened / closed sections | any row with children |

**Row identity:** owner + scope + name for attribute leaves; label path from root for
structural rows. Attribute leaves use their key (not path) because two attributes in one
owner group can share a name across scopes.

Expansion is remembered as **the user's choice** — it overrides the row's default in both
directions (a closed section stays closed; an opened primitive list stays open). Restoring
the selection must not re-open a section the user closed over it.

---

## 7. Colour scheme

Muted, low-saturation throughout. All values RGB 0–255.

### Page occupancy

| Meaning | RGB | |
|---|---|---|
| Active | `110,175,110` | muted green |
| Vacant | `200,200,200` | light gray |
| Temporary | `110,140,205` | muted blue |
| Out of range | `28,28,28` | near-black |

### Attribute page storage

| Meaning | RGB | |
|---|---|---|
| Constant | `200,195,120` | muted yellow |
| Shared | `200,130,185` | muted magenta |
| Hardened | `120,195,195` | muted cyan |
| Unknown | `130,130,130` | neutral gray — an absence of data, not a fourth state |

### Attribute scope (row text)

| Scope | RGB | |
|---|---|---|
| public | `170,200,230` | soft blue |
| private | `228,205,165` | soft amber |
| group | `205,178,222` | soft violet — element groups |
| edge_group | `224,158,178` | soft rose — edge groups (gated by Group Tables, not Groups) |
| primitive_list | `150,205,190` | soft teal |

Structural rows (Index Maps' per-owner rows, Group Tables' per-table rows, Detail Object,
Data Structure Overhead rows, Unaccounted) have no scope entry and use the default text
colour.

### Panel-owned surfaces

These are inside the page grid and delegates — pixels no style touches — so they stay fixed.

| Use | Colour |
|---|---|
| Non-run cells (Continuous block) | `78,78,78` |
| Cell separators | `45,45,45` |
| Default attribute text | `220,220,220` |
| "No pages" text | `180,180,180` |
| Swatch border | `85,85,85` |
| Instanced warning | `224,192,96` (amber) |
| Pause button while frozen | theme's ticked-checkbox roles (fallback `200,129,60`) |
| Sharing-peer row wash | theme's `primary` at alpha 72 (fallback `110,150,220`) |
| Hovered-row border | `100,170,240` (blue) |
| Percentage bar | theme's slider roles (fallbacks `55,55,55` / `70,130,200` / `230,230,230`) |

### Derived values

- Dimmed bars: blend 62% toward grid background.
- Continuous-block run colours: fixed seed, saturation 0.42, value 0.86.

### Colours the panel must NOT set

The tree background, page-grid canvas, status line, legend labels, separators, and resting
Pause button. These inherit from the theme.

---

## 8. Theming

**Do not pin colours.** Houdini ships colour schemes (2 in H21/H22 old UI; 52 in H22's new
UI) generated from HSV triplets in `$HFS/houdini/config/Themes/default.theme.json` and
published as ~50 semantic roles (`pluto_bg`, `pluto_fg`, `pluto_viewSurface`, …). Reference
implementation: `$HFS/houdini/python3.13libs/hutil/qt/pluto/`. A panel that pins a
background or body-text colour opts out of all of them.

SideFX's shipped panels follow the same approach: across 32 non-deprecated Python Panels
there are 8 colour-bearing `setStyleSheet` calls total.

**This panel's only stylesheet** carries typography plus the amber instanced warning.
Section titles use `color: palette(window-text)`. The tree viewport is
`background: transparent`. The page grid takes `QPalette.Window` (not a scroll area's
`Base`).

> **Type-based text colour was evaluated and not adopted.** Houdini's node-info panel
> colours text by type via `hou.ui.colorFromName(f"VopInOut{n}Color")`, but these resolve
> from the legacy `.hcs` scheme, not pluto themes — they don't move with theme changes.
> Revisit if SideFX hooks them up. Note: those calls are GUI-only (no headless testing) and
> use a different vocabulary from the provider's type names.

---

## 9. Behaviour

**Refreshing.** Listen for node selection, parameter changes, input rewiring, cook events,
node deletion, and playbar frame changes. Coalesce these and refresh once, deferred to the
idle loop.

**Never force a cook.** Read the geometry the node already produced.

**Selection clearing and restoring must both reach the panel.** `onNodePathChanged` fires
on path changes, which covers neither: the pane tab continues to point at the last node.
Compare mode requires both transitions through the same entry point, so Pause gates them.

> *Recommended:* `ChildSelectionChanged` callback on the enclosing network, added while
> pinned and retained while the live node is None.

**Errors.** Show the message in the title bar and clear both sections. Don't propagate.

**Teardown.** Remove every callback on close. A deferred refresh already queued must check
the panel is still alive before touching any widget.

**Headless.** Import graphical-only Houdini modules lazily so the module imports for testing
outside a GUI session.

---

## 10. Performance targets

The page grid is the only performance-sensitive part. A million pages is normal.

- Never expand per-slot data for the whole map — decode packed byte streams into per-page
  arrays and expand only visible pages.
- Draw the visible band, not the map — build one image covering visible rows and blit once.
- Target ~5 ms per frame normally, no worse than ~25 ms with thousands of 1 px pages visible.
- Cache decoded per-attribute page arrays; drop on node or output change.

---

## 11. Implementation traps

These are Qt / Houdini interaction problems that an implementer will encounter. They are
separated from the design rules above because they are about *surviving the platform*, not
about what to build.

### Theme change recovery

Having any stylesheet breaks live theme switching: `QStyleSheetStyle::polish()` sets an
explicit palette on styled widgets, and an explicit palette stops inheriting later
application palette changes. The panel goes stale until rebuilt.

Listen on **both** routes: `PaletteChange` on the widget, and a colour comparison in
`paintEvent` (Houdini repaints everything on a scheme change, but may not send an event to
a widget whose resolved palette didn't move).

> Do NOT use an application-wide event filter — it catches the change but fires for every
> event on every object (measured: 2392 calls for one palette change), drove ten
> re-polishes, and froze Houdini for ~10 s.

Recovery procedure:
1. Defer and collapse re-polishes (a scheme change is announced many times).
2. Record the colour polished for (or the paint check fires again for a handled change).
3. Clear explicit palettes with a default-constructed `QPalette` (resets the resolve mask).
4. Re-assign the stylesheet (re-resolves `palette(...)` references).
5. Repaint anything painted by hand.

`style().unpolish()/polish()` alone is not enough — it refreshes the panel but leaves child
viewports on the old palette.

### The tree sits ON the panel

The tree viewport is transparent and the grid canvas uses `QPalette.Window`, so the area
below the last row and the canvas behind cards are the panel's own colour. The dimmed-bar
lookup must be rebuilt against the canvas colour whenever it changes.

### Style-painted item backgrounds

A style may fill each item's panel itself (`PE_PanelItemViewItem`), and H22's new UI does.
Anything drawn *under* a row is silently erased. Draw over, not under.

### Peer wash direction

The peer wash lifts a dark row and darkens a light one — "different from background, toward
accent" is the invariant, not "lighter than background."

### Legacy `base.qss` colour

The area below the last tree row may be filled from `QAbstractItemView { background:
rgb(@ListEntry2@) }` in `base.qss` — a legacy `.hcs` colour that doesn't move with the new
theme. Under `PlutoStyle` alone the style paints that area itself, so the two are
indistinguishable in a full-panel render; test the mechanism directly.

---

## 12. Acceptance checklist

- [ ] Selecting a SOP populates both sections; selecting nothing clears them cleanly.
- [ ] Branch rows are always Attribute Set, Primitive List, Index Maps, Group Tables,
      Detail Object, Unaccounted, in that order.
- [ ] All six branch rows render at every scope-toggle state.
- [ ] Toggling any scope leaves every total unchanged.
- [ ] All seven scope checkboxes start on.
- [ ] Unchecking Attribute Set hides Public, Private, and Groups; re-checking restores them.
- [ ] Group Tables' Point/Primitive/Vertex/Edge rows individually disappear at 0 bytes even
      with the toggle on; Index Maps' four per-owner rows never disappear at 0 bytes.
- [ ] Edge groups render in their own scope colour and stay visible whenever Group Tables'
      toggle is on, regardless of the Groups toggle.
- [ ] The Nth bar matches the Nth toggled row after sorting by any column.
- [ ] A row toggled on one node stays on when another node with that attribute is selected.
- [ ] The selected row and any collapsed section come back after a node switch, a re-cook
      and an un-pause.
- [ ] A manually resized column survives a scope toggle, a node switch, and un-pausing.
- [ ] Clicking a row emphasises its bar; hovering a bar outlines the row without changing
      the selection.
- [ ] The Pages column shows three slots, with dashes where the split is unmeasurable.
- [ ] Primitive-type rows start collapsed.
- [ ] Pause freezes selection, cook and timeline updates; un-pausing catches up.
- [ ] The instanced warning appears only when the report says so.
- [ ] Closing the panel removes all callbacks and produces no errors afterwards.
- [ ] Paused, selecting a node and pressing Reload reads *that* node and stays paused.
- [ ] Pin leaves the pause state alone, in both directions.
- [ ] Deselecting with a pin held shows the pinned node's own report.
- [ ] Pinned with the same node selected, stepping a frame reads frame to frame.
- [ ] The page grid is hidden in every compare state and comes back with previous splitter
      sizes on un-pin.
- [ ] The six branch rows keep their order under both sort directions.
- [ ] An attribute with no readable page table shows a dash in Δ Pages and Δ c/s/h.
- [ ] An instanced side dashes Δ New and Δ Unique on every row; Δ Total still reads.
- [ ] An added / removed / replaced row is styled across every column with no row colour.
- [ ] Δ Pages on an owner row equals its index map's page-count change.
- [ ] A row differing only in `data_id` is not drawn.
- [ ] Deleting the pinned node leaves the comparison working.
- [ ] Emptying selection falls back to pinned report; re-selecting brings comparison back.
- [ ] Pin sits left of Reload, disabled until there is a report.
