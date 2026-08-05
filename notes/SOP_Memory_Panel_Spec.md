# SOP Memory Panel — build specification

How to build the SOP Memory Python Panel (Qt widgets) from scratch. Everything the panel
displays comes from one provider call; this document describes what to draw, where, and how
it responds. It does not assume any existing Python.

**Provider:** the `_page_tools` extension built from `src/_page_tools.C`. Its one entry point
is `report(node_path, output_index=<view output>)`, returning a plain
dict. The full key layout is the schema block at the top of that source file — read it first;
every number below is one of those keys.

**Panel mechanics:** follow Houdini's shipped Python Panel examples for the interface XML and
the `onCreateInterface` / `onNodePathChanged` / `onDestroyInterface` entry points.

---

## 1. What it is

A diagnostic panel that answers three questions about the geometry a SOP just cooked:

1. **Where did the memory go?** A breakdown tree — per attribute, per group, per owner,
   plus the containers that hold them.
2. **How much of it is this node's fault?** Every row carries three figures: total, how much
   was newly allocated here, and how much this geometry uniquely owns.
3. **How is it physically laid out?** A visual map of the element pages, showing holes from
   deletions and how each attribute's page data is stored.

It never modifies geometry and never forces a cook of its own.

---

## 2. Layout

Two stacked sections in a draggable vertical splitter, with a title bar above them.

```
┌────────────────────────────────────────────────────────────────────┐
│ /obj/geo1/blast1                    [Output ▾]  [Pin]  [❚❚ Pause]  │  title bar
│ ⚠ Instanced — the output is fully shared with another detail…      │  (only when true)
├────────────────────────────────────────────────────────────────────┤
│ ▼ Memory                                                           │
│   Scopes: ■public ■private ■groups ■primlist □internal             │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │ Component        %      Total    New   Unique  ID  Pages  Type│  │
│  │ ▼ Geometry Memory ███ 12.8 MB  4.1 MB  12.8 MB                │  │
│  │   ▼ Attributes & Groups ██ 11.9 MB …                          │  │
│  │     ▼ Point       ██   8.2 MB …                               │  │
│  │         P         ██   4.1 MB …        12  0/4/0  numeric[3]  │  │
│  │         Cd        █    2.1 MB …        17  0/4/0  numeric[3]  │  │
│  │     ▶ Primitive   █    3.7 MB …                               │  │
│  │   ▶ Internal      ▏  102 KB …                                 │  │
│  │     Unaccounted   ▏    6.5 KB …                               │  │
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

**Sizing:** panel margin 4 px. The splitter starts at roughly 260 px for the top section and
520 px for the bottom, remembers wherever the user drags it, and neither section can be
collapsed to nothing by dragging. Collapsing a section by its header gives all the space to
the other one; re-opening restores the remembered split.

**Section headers** are bold 13 pt with a disclosure triangle; the panel title is bold 17 pt.

---

## 3. Title bar

* **Left:** the SOP's path. If nothing is selected, "No SOP node selected". If the provider
  raised an error, show the path and the error message here instead.
* **Output selector:** a dropdown listing the node's output names. Hidden for single-output
  nodes. Resets to the first output when the node changes. Choosing one re-reports that
  output.
* **Pin button:** a toggle. Captures the displayed node's report as the A side and puts the
  panel into compare mode (§6). Disabled while there is no report to capture. Reads `Pin` /
  `Pinned`, bold while held, following Houdini's convention for a control off its default.
  Placed left of Reload. It does not change the pause state.
* **Reload button:** present only while paused. It adopts the node selected since the panel
  was paused, reports that node, and leaves the panel paused. With no selection outstanding it
  re-reports the current node. It is the only route by which a paused panel reads new
  geometry.
* **Pause button:** a toggle. When on, the panel stops following node selection, parameter
  edits and timeline changes — for comparing two panels side by side, or for parking on a
  node whose geometry is expensive to re-cook. It keeps its label and icon in both states and
  only changes colour, so the button doesn't jump. Un-pausing applies whatever selection
  arrived while frozen. Colour it as a **ticked Houdini checkbox**, since that is what it is
  — `checkedBase` filled, `checkedFg` for the mark, and a border blended a quarter of the way
  from the fill toward the mark (`hutil/qt/pluto/parts.py`, `_setupCheckColors`) — with the
  label **bold**, Houdini's convention for a control that is no longer at its default. That
  styling is carried **only while the toggle is on**, applied to the button and then cleared,
  so the resting button carries no rule of ours and is drawn like every other button.
  **Open:** under H22's new UI the resting button is reported as looking inverted. Two Qt
  mechanisms were measured and both came back negative — an ancestor stylesheet neither
  displaces the base style's `drawPrimitive` nor rewrites the button's inherited palette — and
  removing our rule did not change it. With no rule left, what remains is a seam in Houdini's
  own theming (new-UI chrome around a legacy-painted button), which shipped panels share; it
  is not something this panel can correct.
* **Instanced warning:** an amber line directly under the title, shown only when the report
  says the output detail is literally another node's detail. Word it plainly: this node
  allocated nothing of its own, and the total shown belongs to the shared geometry.

---

## 4. The memory tree

A single tree rooted at **Geometry Memory**, whose figures are the whole-geometry totals.
Three fixed children in this order:

| Row | Contains |
|---|---|
| **Attributes & Groups** | The actual data. One sub-row per owner (Point, Primitive, Vertex, Detail), each listing its attributes and groups. Primitive also carries the **primitive list**, which is not an attribute. Plus an **Edges** sub-row when edge groups exist. |
| **Internal** | The bookkeeping that holds the data: the attribute container's own overhead, the index maps (one child per owner), the group tables (one child per table), and the geometry struct itself. |
| **Unaccounted** | What the provider could not attribute to anything reachable. Usually zero. |

### Where the consumer does its own arithmetic

Every displayed figure comes from the provider, which computes it. The panel renders; it
does not derive. There are exactly two exceptions, and both exist because the panel groups
things differently from the way the report is structured — not because a number is missing.

**The primitive list.** The report carries it at the top level, as `primitive_list`, beside
`owners`. The panel shows it as a row under **Primitive**, because that is where a reader
looks for it. `breakdown()` adds its bytes into the Primitive owner row and into the
Attributes & Groups parent.

**Edge groups.** The report puts them inside the edge group table, because a `GA_EdgeGroup`
is owned by that table and is not an attribute. The panel moves them out to an **Edges**
row beside the owners, since on real geometry they are data rather than bookkeeping — 240 KB
for three groups on a 40×40 grid. `Internal > Group Tables > Edge` is then reduced to the
table's name→group map, which the provider reports as `name_map_*_memory`. The subtraction
that takes the group bytes back out of `group_tables_*_memory` is the panel's.

In both cases the bytes move with the rows: Attributes & Groups gains exactly what the other
category gives up, and the three top-level categories still sum to the root total. Neither
exception invents a figure — each is a regrouping of figures the provider already supplies.
Anything beyond these two belongs in the provider.

### Columns

`Component | % | Total Memory | New Memory | Unique Memory | Data ID | Pages c/s/h | Type`

* **%** is a small horizontal bar with the percentage drawn over it, sized against the
  geometry total. Give the column a minimum width of ~72 px. Colour it like a Houdini
  **slider**, which is what it resembles: groove in `field`, filled part in `primary` — the
  two roles `hutil/qt/pluto/parts.py` `drawSliderGroove` uses. Draw the text twice, clipped
  to each half and in that half's foreground role (`primaryFg`, `fieldFg`): no single colour
  stays legible over both, since some schemes make the accent darker than the groove and
  some lighter.
* **Memory columns** show human-readable sizes ("4.1 MB"), but sort by the real byte count,
  never by the formatted string.
* **Total Memory** on an attribute row gains a suffix when the provider says another
  attribute in the same detail references some of the same bytes — a copy-on-write sibling:
  `4.1 MB (4.0 MB shared)`. Omit it entirely when there is no sharing (not "(0 shared)").
  It exists to explain an addition discrepancy: attribute rows can exceed their owner's row,
  because each honestly reports bytes it shares with a sibling while the owner counts them
  once. The suffix is **display only** — it must not reach the sort key, or two rows at the
  same total would separate purely because one of them shares.
* Selecting an attribute row **shades the rows it shares with** — a soft wash on the row
  background, not an outline: an outline on every peer is loud, and a background says "these
  go with the selected row" more quietly. It is a different channel from the bar-hover
  border, so a row that is both shows both, and selection keeps its own fill on top. Peers
  can be hidden by the scope filter or sit under another owner; skip those rather than
  failing.

  Two properties this has to keep. It must be **the theme's accent** (`pluto_primary`, one of
  the three HSV values each scheme is generated from) rather than a colour of ours, and
  deliberately not the *highlight* role, which is what selection uses. And it must start at
  the **row's content**, i.e. the item rect, not at the viewport edge — a quiet cue reaching
  further left than the loud one reads as a mistake. How far left a style paints *selection*
  is a style decision and not something to match against: `PlutoStyle` starts at the content,
  while others span the indentation.
* **Data ID** is the raw id, or "-" if unset. Render it *italic* when the report says this id
  was also found on an input — meaning this node passed the values through rather than
  changing them. Do not add a text suffix; italics keep the column scannable. Tooltip should
  say it describes values, not storage: such an attribute can still have new memory.
* **Pages c/s/h** always shows three slots so the numbers line up under the header letters:
  * `12/3/1` — measured constant / shared / hardened counts
  * `12/-/-` — the split was **not measurable**; a dash means unknown, never zero
  * `-` — nothing about this attribute's pages is readable (its page table is
    private, or — for blind data and a full-representation primitive list — it has none)
* **Type** shows the attribute type with its tuple size (`numeric[3]`). Append `(tail-init)`
  when the report flags it. The synthetic primitive-list row shows `paged primitive list` or
  `full primitive list` instead — which of the two explains everything else on that row.

### Row rules

* **Bold** any row that allocated new memory (its New figure is non-zero).
* **Text colour is the attribute's scope** (see §8). Structural rows take the default text
  colour.
* Rows sort within their group when a header is clicked; owner rows and the three category
  rows keep their fixed order regardless. **The Pages column is not sortable** — its numbers
  can't rank rows meaningfully, so clicking it does nothing.
* The **primitive list** appears as a row under Primitive, not as a category of its own —
  a display choice, not a claim about the data: the provider reports it as its own term,
  and the panel adds its bytes into that owner and the category, exactly as it does for
  edge groups. When
  the geometry has per-primitive objects it gets child rows, one per primitive type, showing
  each type's share and count. **Those children start collapsed** — they are detail about one
  row, and there can be many. They do not sum to their parent, by design.

### Scope filters

Five checkboxes above the tree: **public, private, groups, primlist, internal**. Each has a small
swatch in its scope colour. All are on by default *except* internal.

These hide rows only. **They must never change a total** — an owner row can legitimately show
more than the sum of its visible children. Deriving totals from visible rows is the classic
bug here: hidden bytes silently vanish and reappear in Unaccounted.

---

## 5. The page grid

Geometry stores elements in fixed pages of 1024 slots. This section draws one **card** per
page, wrapped into as many columns as fit, scrolling vertically.

Each card is a **32×32 block of cells** — one cell per slot — drawn at the chosen zoom
(1–10 px per cell; default 3, so a card is 96×96). Cards are separated by 10 px horizontally
and 14 px vertically, with a 2 px left margin. At 4 px per cell and above, draw thin
separators between cells; at 8 px and above make them 2 px.

**Controls:** Owner dropdown (Point / Primitive / Vertex / Detail), Mode dropdown, Cell size
dropdown (1, 2, 3, 4, 6, 8, 10 px), and a Legend checkbox.

### Modes

* **Occupancy** — colour each slot by whether it holds a live element, is a hole left by a
  deletion, or is transient. Slots past the end of the last page are drawn near-black. This
  is how you see fragmentation: a blasted grid shows scattered gaps.
* **Continuous block** — colour each *run* of consecutive live elements a different soft
  hue, everything else neutral gray. This shows how badly the element ordering has been
  broken up. (Ask the provider for block ranges when in this mode.)
* **Memory block sharing** — for the attribute *selected in the tree*, colour each page by
  **which memory block it sits on**. Two pages of one colour are one allocation, whether
  they are two pages of this attribute or one page each of two attributes. The card is one
  flat colour here: the block is a property of the whole page, not of individual slots.

  **Each tile carries its block id, in base 36.** Colour alone runs out well before the
  block counts real geometry reaches: the palette is one Oklch ring, so N blocks are N hues
  spread over a circumference of 0.75 ΔE, and past roughly 32 of them neighbouring ids are
  the same colour to the eye. The label is the same id the status line names, so a tile and
  the card under the cursor read as one fact — and **the card must use the same base**, or
  reading across the two becomes a conversion.

  Base 36 because a block id is a *link* and nothing else. The provider hands them out in
  the order it walks the pages, so block 41 sitting beside block 42 says nothing about
  either, and a decimal label invites the reader to believe it does. Letters read as
  identifiers. Store them as integers in the report; the base is a display decision.

  Draw it in Houdini's own UI font (Lato, `$HFS/houdini/fonts`), sized to the widest id in
  the report so the labels do not vary tile to tile — one that shrank as the id grew would
  read as a difference between blocks rather than a difference in length. Lay the glyphs
  out on their **real advances**, not on a fixed cell: `i`, `j` and `l` advance 3 px where
  `m` advances 11, and `4l` set on the wider step reads as two tokens.

  Two omissions, both deliberate. **Block 0 is never labelled**: it means "no other page
  reaches this block", which is not a block anyone can look up, and an id would invite them
  to. And a tile too small for a readable size gets no label rather than a smear.

  **No cell separators.** Every other mode rules the tile into its 32×32 slots at high
  zoom. The block is a property of the whole page, so here the tile is one flat colour and
  those lines would divide it into slots that differ in nothing — while crossing the
  label.

  **The ink is chosen per block, from that block's own lightness** — the neutral, dark or
  light, that its tile separates further from in Oklab L. One fixed ink survives only as
  long as the palette keeps one lightness, and the day it does not the failure is invisible
  until someone looks at the wrong end of the ring. When a block is pinned, the labels on
  the other tiles blend toward the canvas by the same amount their tiles do, so a label
  never stays louder than what it sits on.

  **A pinned block's tiles are ringed** in the theme's accent (`pluto_primary`, the same
  colour the peer wash uses — never `highlight`, which is selection, and never a literal).
  Dimming the rest already says which tiles are on the block, but it says it by absence,
  and on a grid where most tiles are off-block anyway the eye reads the wash rather than
  the few that escaped it. Keep the ring thin at every zoom: it marks a tile, it does not
  decorate one.

  Block identity, not an index-aligned comparison. Page 0 and page 8 of a self-merged
  attribute are one block, as are page 0 of `id` and page 8 of `id_copy`; neither relation
  survives comparing pages at matching indices, which is what the mode used to do.

  This mode is driven by the **selection**, not the checkboxes — the toggles have no meaning
  in it. When nothing is selected, draw nothing and say *"Select an attribute"*. When the
  selected attribute has no page data, or shares nothing, say which — the cases are
  different problems and a single "nothing to show" leaves the user guessing.

  **Every one of those messages must be guarded by the condition it describes.** A final
  unconditional `else` turns the message into a claim about whatever case was not
  anticipated: while constant pages were excluded above, a numeric attribute sharing only
  constant pages fell through to the dict/string wording and was told it shared through a
  value table it does not have. Where nothing explains the absence, say that and no more.

  **Constant pages are drawn like any other.** A constant page is never hard, so the page
  API cannot say whether it is refcounted; the provider settles it by matching the page
  pointer against the blocks `countMemory()` reported. A grey card in this mode means the
  measured answer "no other page slot in the detail reaches this block" rather than "could
  not be determined". A page that owns no
  allocation — compressed to zero, or narrow enough to be stored inline — has nothing to
  share and correctly takes that grey.

  An attribute that shares a block with **another of its own pages** enters the mode with
  no bars at all: that relation is in the tiles, as two pages of one colour, and needs no
  row of its own. An attribute with no siblings still has something to show and must not be
  told it shares nothing.

  **Clicking a page highlights every other page on its block**, by dimming the pages that
  are not — the whole card, tile and bars together, so a dimmed page does not keep bright
  bars. It stays until it is cleared, and because the block ids are detail-wide it survives
  selecting a different attribute: highlight a block on `id`, select `id_copy`, and its
  pages on that same allocation stay lit. Clicking the highlighted block again, or a page
  whose block nothing else reaches, clears it.

  **A click, never a hover.** Highlighting repaints the whole grid, so following the cursor
  with it makes the mode restless — flashing through one block after another as the mouse
  crosses the cards, none of them asked for. Hover fills the status line and nothing more.

### Generated palettes

Three things are drawn "a different colour each": continuous-block runs, memory blocks, and
the per-attribute bar colours. All three come from one generator, under three seeds, and it
has two requirements.

**Index *i* must keep its colour however many colours were asked for.** The count is data
dependent — one more block id, one more attribute — and a palette that reshuffled on the
Nth would repaint cards that had not changed. So the hues are a *sequence* whose term *i*
does not depend on the length: the **van der Corput sequence** (bit-reversal), offset by a
per-seed start.

**Distinct indices must look distinct.** Independent random hues fail this badly: they clump
by nature, giving two indistinguishable greens at four colours and outright duplicates by
twenty-four. Van der Corput refines by halving, so after *n* points the largest gap is
1/2^⌈log2 n⌉ and there is no clumping to have. Measured against the golden-ratio sequence —
the other prefix-stable choice — it separates 1.4× better at 8 colours and 2× at 64.

Hues are placed on a constant-lightness, constant-chroma ring in **Oklch**, so equal steps
around the circle are equal steps to the eye; HSV's are not, its yellows being far lighter
than its blues at one S/V. The ring's chroma is chosen to be inside sRGB at *every* hue, so
nothing needs gamut mapping and no two hues can clip onto one colour. Houdini ships
`coloraide`, but the panel does the conversion itself in numpy — this runs on the paint path
— and the test suite checks the result against `coloraide` so the shortcut stays honest.

  **Dict and string attributes will read as sharing nothing even when they share tens of
  megabytes.** Their pages hold *handles* into a value table, and a copy allocates a fresh
  handle array while sharing the table. Say so on the status line and point at the Total
  column; drawing an empty bar next to a Total that reads "(11.4 MB shared)" is the one
  outcome that actively misleads.

### Attribute bars

Under each card, draw one thin horizontal bar per attribute the user has toggled on — same
width as the card, height equal to the cell size (minimum 4 px), 4 px below the card and 6 px
apart. Each bar colours that page by how the attribute's data is stored there: constant,
shared, hardened, constant-and-shared, or unknown.

**Constant-and-shared is its own colour, not a shade of either.** A constant page whose
repeated value is wide enough to need its own allocation gets refcounted, so it is both
compressed and held in common. Colouring it "constant" claims the detail owns it outright;
colouring it "shared" claims a full page of data is held in common. Only pick this colour
where the provider reports the page in both `constant_page_bits` and `shared_page_bits`.

In **Memory block sharing** mode the bars mean something else: one row per *peer* of the
selected attribute, filled only on the pages whose block that peer is also on, and drawn in
**that attribute's own colour**. A page two attributes hold therefore shows two bars in two
different colours. Hovering a bar still outlines its attribute's row in the tree, which is
how the user reads which peer a bar belongs to.

**A bar's colour is fixed detail-wide**, assigned from a stable order over every attribute
in the report rather than per selection. Comparing one card against another is the reading
this mode exists for, and a palette rebuilt per selection would make the same attribute two
colours.

**Peers on another owner are drawn like any other.** The relation is block identity, which
needs no page-to-page correspondence, so a primitive attribute can light a bar under a point
attribute's page. A bar therefore carries its own owner: resolving its tree row by assuming
the grid's owner is wrong the moment such a bar is drawn.

**The Nth bar must always be the Nth toggled row as it currently appears on screen.** Take
the order from the tree, not from a separate sort — otherwise re-sorting the tree silently
mislabels the bars.

Attributes get a checkbox in the tree only if they have page data — the provider reports
that as a page-details block that is either filled in or absent entirely. Rows without one,
and without child rows, get a blank spacer so their names still line up. A row that has children
but no checkbox does *not* get the spacer — its disclosure triangle already occupies that
slot.

### Legend and status

The legend is a row of 14 px swatches with labels, hidden unless the checkbox is on. It shows
the current mode's colours, plus the storage colours when any bar is visible. Two swatches
are conditional, for different reasons. "unknown" must not appear unless something on
screen actually has unknown pages, or its absence stops meaning anything.
"constant, shared" is simply narrow, and the legend stays shorter without it.

**Legend labels are a hard floor on the panel's width.** The row does not wrap or elide, so
its `minimumSizeHint` is its `sizeHint` and the panel cannot be dragged narrower than the
widest mode's legend. Keep each label to the shortest phrase that still *stands alone* — do
not name the mode (the Mode combo is a few pixels away), and do not restate in one entry
what the next entry already says.

In Memory block sharing both channels hand out a colour per thing, so they take **one**
entry — "unique colour per memory block / attribute" — rather than repeating the phrase
either side of the separator. The block id stamped on each tile gets no entry of its own:
hovering any tile names it (`block 3`) on the status line, and the legend row is a hard
width floor that an entry restating what one hover already says is not worth. Its "/ attribute" half is conditional on bars being drawn: an
attribute that only reuses its own blocks has none, and naming a channel that is not on
screen sends the user looking for it.

The status line under the grid shows whatever is under the cursor:

```
point page 37 [37888 - 38911]   active 1024   temporary 0   vacant 0     (over a card)
P [public]   page 37 [37888 - 38911]   shared                            (over a bar)
id / id_copy [point public]   page 37 [37888 - 38911]   shares this page's block
point page 37 [37888 - 38911]   block 3, also on pages 8, 16   (a card, sharing mode)
```

**The slot counts belong to Occupancy mode.** In Memory block sharing the card answers
*which block*, and three occupancy figures beside that answer crowd out the one the user
switched modes to get. A page whose block nothing else reaches says so — the same wording
as the legend's grey swatch — rather than falling silent, which would read as unanswered
instead of measured.

**Every line that names a page names the offsets it covers**, so a card can be read against
an element number without arithmetic. These are *offsets*, not point or primitive numbers —
on a fragmented index map the two diverge, which is what Occupancy mode exists to show — and
the range stops at the map's end, since the last page is usually partial: a detail with
8,768 offsets ends `page 8 [8192 - 8767]`, not `[8192 - 9215]`.

In Memory block sharing mode a card's line names the block and the other pages on it,
truncated with a count — a 256-way self-merge puts every page of the source on one block,
and the status line is one line.

When the grid has no pages, draw "No pages (empty geometry or no node selected)" centred in
light gray.

---

## 6. Compare mode

Pin a node's report, then read whatever is selected against it. The comparison is the memory
tree of §4 with delta columns; the page grid takes no part in it.

### Pin

Pin captures the displayed node's report as the **A side**. The captured report is fixed: it
is not re-read, does not follow its node, and remains valid after that node is deleted. Pin is
disabled while there is no report to capture.

Pin does not change the pause state, and Pause does not change the pin state.

| | the live side |
|---|---|
| pinned, not paused | follows selection |
| pinned, paused | changes only on Reload or un-pause |

### What the body shows

| Condition | Body |
|---|---|
| pinned, no node selected | the pinned node's report, in the §4 columns |
| pinned, node selected | the delta tree |
| pinned, node selected, no row differs | `No differences.` |

Deselecting shows the pinned node's report; re-selecting any node, including the pinned one,
returns to the delta tree. The A side does not follow the playbar, so holding the pinned node
selected and changing frame compares one frame against the frame pinned.

### Layout while pinned

The Index Map Pages section is hidden in every state of this mode, including the pinned
node's own report. Un-pinning restores it at the splitter sizes stored before the mode was
entered.

The per-attribute bar checkboxes are hidden with it.

### Title bar and notices

```
/obj/geo1/grid1  →  /obj/geo1/blast1     [Output ▾]  [Pinned]  [⟳ Reload]  [❚❚ Paused]
```

Pin is a text toggle reading `Pin` / `Pinned`, bold while held, following Houdini's convention
for a control off its default. It is placed left of Reload. With no live node the title reads
`/obj/geo1/grid1   (pinned)`.

The live side keeps its own output selector. The A side's output index is fixed at pin time.

A **pending-selection line** is shown, in the position the instanced warning occupies,
whenever a selection has been made while paused and not yet applied:
`blast1 selected — Reload or un-pause to read it.`

The **instanced warning identifies which side is instanced**, and that side's effect on the
Δ New and Δ Unique columns is specified below.

### The delta tree

Every memory figure is a delta. There are no absolute columns and no percentage column.

```
Component | Scope | Δ Total | Δ New | Δ Unique | Δ Pages | Δ c/s/h | Data ID | Type
```

* **Δ Total, Δ New and Δ Unique** are each B − A over the figure the provider reports for that
  row. The A and B totals are supplied as the tooltip on the Δ Total cell.
* **Δ Pages** is the change in the index map's `num_pages`. It is not the sum of `Δ c/s/h`:
  those three counts partition the pages only when `has_hardened_details` is true. It is
  carried on the owner rows and their index maps, and repeated on each attribute under an
  owner.
* **Every delta carries an explicit `+` or `−`.** Direction must not depend on colour alone. A
  measured zero is rendered as `0`, dimmed.
* **Structural rows hold their canonical order under any sort**, as owner rows do in §4.
* **Δ c/s/h and Data ID are not sortable.** Neither a three-part cell nor a paired `19 → 35`
  yields a ranking.

### Rows with no difference are not drawn

Retain a row when it differs, and every ancestor of a retained row. An ancestor is retained
even when its own delta is zero, since two children may offset.

A difference in `data_id` alone does not retain a row: a node that rebuilds its geometry
renumbers every attribute in it. The column is still populated with `A → B` on rows retained
for another reason.

### A delta against an unknown is unknown, never zero

The dash rule of §4 applies unchanged. Unknown on **either** side makes the slot unknown.

| State | Single node | Diff |
|---|---|---|
| no `page_details` (array, blob, index-pair) | `-` | `-` in both `Δ c/s/h` and `Δ Pages` |
| `has_hardened_details` false (element groups, paged prim list) | `2/-/-` | `Δ/-/-` — the constant count is known, the split is not |
| present on one side only (added / removed) | — | signed full counts, which are measured, not unknown |

A `has_hardened_details` that differs between A and B leaves the affected slots unknown; it
must not be rendered as a shared-to-hardened change.

**An instanced side empties Δ New and Δ Unique on every row**, in both columns, whichever side
it is. The provider reports zero for those two figures on an instanced detail, so no delta
taken from them is defined. Δ Total is unaffected.

An Attribute Wrangle is an HDA and reports as instanced; under H22 so does `color`.

### A row that changes type

One `(owner, scope, name)` may hold a numeric attribute in A and a string attribute in B. Such
a row is given the state **replaced**, is rendered as a single paired row, and its `Type` reads
`numeric[1] → string[1]`.

Its `Δ c/s/h` carries the measured counts. The dash is reserved for "not measurable" and must
not be reused for "not comparable".

### Row state and colour

Growth and shrink take a hue pair of their own, distinct from the scope palette and the
page-storage palette. Unknown takes the neutral grey §5 uses for an absence of data.

The three row states are carried by **font, not colour**, and are applied to every column of
the row:

| | |
|---|---|
| **bold** | added — present on the B side only |
| ~~strikethrough~~ | removed — present on the A side only |
| *italic* | replaced — same key, a different attribute |

These rows take **no row background**. Row backgrounds are not a surface this panel paints
(§8), and the peer wash already uses that channel.

This mode has **no legend**. Every colour it uses is accompanied by a non-colour channel: the
delta hues by their sign, unknown by its dash, a measured zero by its `0`, scope by the Scope
column, and a replaced row by `A → B` in Type.

---

## 7. Interaction

| Action | Result |
|---|---|
| Select a SOP | Panel re-reports and redraws (unless paused) |
| Node cooks, parameters change, timeline moves | Same |
| Tick an attribute's checkbox | Adds its bar under every card |
| Click an attribute row | Its bar is emphasised; all other bars dim toward the background |
| Click a row whose bar isn't shown | Status line nudges: "toggle *name* on to see its bar", or "switch Owner to *X*" if it belongs to a different owner |
| Hover a page card | Status line shows that page's offset range, plus its occupancy counts — or, in Memory block sharing mode, its block and the other pages on it. Never repaints the grid |
| Click a page card | Memory block sharing mode only: highlights that block — its other pages keep their colour, the rest dim. Clicking it again clears it |
| Hover an attribute bar | Status line shows that attribute and its storage on that page, **and the matching tree row gets a coloured border** |
| Click a column header | Sorts attribute rows within their owner group |
| Toggle a scope | Hides/shows those rows and their bars; totals unchanged |
| Collapse a section | The other section takes the space; re-opening restores the split |
| Change Owner / Mode / Cell | Redraws the grid |
| Press Pause | Freezes everything until un-paused |

**Two separate highlights, deliberately.** Clicking a row *selects* it — a persistent
background highlight plus bar emphasis. Hovering a bar *outlines* the matching row with a
border. Hovering must never disturb the selection.

**The user's view of the tree persists across nodes.** Rebuilding the tree — a different
SOP, a re-cook, un-pausing — must not throw away where they were looking. Three pieces of
state survive it, all restored by row identity:

| state | remembered for |
|---|---|
| bar toggles | attribute rows |
| the selected row | any row |
| opened / closed sections | any row with children |

**Row identity** is owner + scope + name for an attribute leaf, and the label path down
from the root for a structural row. Structural rows have no key of their own, but
`Row.label` *is* their identity and labels are unique among siblings, so the path names
the row; `note` is display-only and must never enter it. Attribute leaves use their key
rather than a path because two attributes in one owner group can share a name across
scopes. A remembered row that does not come back simply never matches.

Expansion is remembered as **the user's choice**, not as a list of collapsed rows: it
overrides the row's default in both directions, so a closed section stays closed *and* an
opened `<primitive list>` stays open. Restoring the selection must not re-open a section
the user closed over it — the later choice wins.

---

## 8. Colour scheme

Muted, low-saturation throughout — the panel is read for a long time and harsh colour is
tiring. All values are RGB 0–255.

**Page occupancy**

| Meaning | Colour | |
|---|---|---|
| Active (live element) | `110,175,110` | muted green |
| Vacant (hole) | `200,200,200` | light gray |
| Temporary | `110,140,205` | muted blue |
| Out of range | `28,28,28` | near-black |

**Attribute page storage**

| Meaning | Colour | |
|---|---|---|
| Constant (compressed) | `200,195,120` | muted yellow |
| Shared (copy-on-write) | `200,130,185` | muted magenta |
| Hardened (solely owned) | `120,195,195` | muted cyan |
| Unknown (not measurable) | `130,130,130` | neutral gray — **deliberately not a hue**, because it is an absence of data, not a fourth state |

**Attribute scope (row text)**

| Scope | Colour | |
|---|---|---|
| public | `170,200,230` | soft blue |
| private | `228,205,165` | soft amber |
| group | `205,178,222` | soft violet |
| primitive_list | `150,205,190` | soft teal |
| internal | *(none)* | default text colour |

**Surfaces we paint ourselves.** These are inside the page grid and the delegates — pixels
no style will ever touch — so they stay fixed.

| Use | Colour |
|---|---|
| Non-run cells in Continuous block mode | `78,78,78` |
| Cell separators | `45,45,45` |
| Default attribute text | `220,220,220` |
| "No pages" text | `180,180,180` |
| Swatch border | `85,85,85` |
| Instanced warning | `224,192,96` (amber) |
| Pause button while frozen | the theme's ticked-checkbox roles (fallback `200,129,60`) |
| Sharing-peer row wash | the theme's `primary` accent at alpha 72 (fallback `110,150,220`) |
| Hovered-row border | `100,170,240` (blue) |
| Percentage bar | the theme's slider roles — see below (fallbacks `55,55,55` / `70,130,200` / `230,230,230`) |

**Colours the panel must NOT set:** the tree background, the page-grid canvas, the status
line, the legend labels, the separators, and the resting Pause button. See Theming below.

**A colour scheme changed while the panel is open must reach it.** Having *any* stylesheet
breaks this on its own: `QStyleSheetStyle::polish()` sets an explicit palette on the widgets
it styles, and an explicitly-set palette stops inheriting later application palette changes,
so the panel goes stale until it is rebuilt (which is what closing and reopening it does).
Worse, Qt has no reason to announce an application palette change to a widget whose own
resolved palette did not move, so the panel can sit on the old scheme with **no event
arriving at all** — measured in a real session, where the tree and its rows repainted
correctly (Houdini's style reads the live `pluto_*` properties, not the palette) while the
panel background, the grid canvas and the section titles kept the previous theme, those three
being the palette-derived ones.

So listen on **both** routes: `PaletteChange` on the widget, and a colour comparison in
`paintEvent`, since Houdini repaints everything when the scheme changes. Do **not** reach for
an application-wide event filter: it catches the change, but it runs for every event of every
object in the application — measured at 2392 calls for one palette change with 300 extra
widgets on screen — and drove ten re-polishes, freezing Houdini for ~10 s per switch. The
re-polish must also be **deferred and collapsed** (a scheme change is announced many times
over, and each re-polish walks the whole subtree), and it must record the colour it polished
for, or the event route leaves the paint check to fire again for a change already handled.
Then, to recover:
clear the explicit palettes with a default-constructed `QPalette` (that resets the resolve
mask rather than pinning anything) **and** re-assign the stylesheet, which re-resolves its
`palette(...)` references. `style().unpolish()/polish()` is not enough — it refreshes the
panel but leaves child viewports on the old palette. Finally repaint anything painted by
hand, since nothing else will ask it to.

**Section titles** take `color: palette(window-text)` so they match the panel title rather
than the button text colour. A `palette(...)` reference asks the theme for a role, which is
the one form of colour a stylesheet here may carry.

**The tree sits ON the panel.** Its viewport is `background: transparent`, and the page grid
takes `QPalette.Window` rather than a scroll area's default `Base`, so the area below the
last row and the canvas behind the cards are the panel's own colour. Neither sets a colour:
one names a palette role, the other says "show what is behind".

The grid canvas comes from the viewport's palette, and the dimmed-bar lookup is rebuilt
against it whenever it changes — dimmed bars blend *toward* the canvas, so a lookup built
against a stale dark grey would make them the loudest thing on a light scheme. The
"does not share it" legend swatch reads the same live colour, or the legend stops matching
the grid.

**Derived:** dimmed bars blend their colour 62% toward the grid background. Continuous-block
run colours are generated from a fixed seed at saturation 0.42 and value 0.86, so the same
geometry always produces the same colours.

**Theming — do not pin colours.** Houdini's UI ships colour schemes: two in H21 and H22's
old UI (UI Dark / UI Light), and **52** in H22's new UI, generated from `base`/`primary`/
`highlight` HSV triplets in `$HFS/houdini/config/Themes/default.theme.json` and published as
~50 semantic roles (`pluto_bg`, `pluto_fg`, `pluto_viewSurface`, …) — the reference
implementation is `$HFS/houdini/python3.13libs/hutil/qt/pluto/`. A panel that pins a
background or a body-text colour opts out of every one of them.

SideFX's own panels do not pin, and that is the whole of their theming strategy: across the
32 non-deprecated, non-QtQuick shipped Python Panels there are **8** colour-bearing
`setStyleSheet` calls in total, while 42 `setStyleSheet` calls merely re-apply
`hou.qt.styleSheet()` to a popup menu. The Viewer Handle Browser sets no colour beyond its
error/warning/info message brushes and carries no palette or theme code at all.

So this panel sets none either. `Style.PANEL_QSS` carries typography plus the amber
"instanced" warning; the tree, the status line, the legend labels, the separators and the
resting Pause button are left to whatever scheme is running. The fixed values in the table
above survive because they are the equivalent of those message brushes — they encode what a
pixel *means*, are keyed to the legend, and no style would paint them.

**Type-based text colour — looked at, deliberately not adopted (2026-08-08).** Houdini's
node-info/geometry panel colours attribute text by data type without a literal in sight:
`hou.ui.colorFromName(f"VopInOut{n}Color")` for `Int`, `Float`, `Vector`, `String`, `Dict`,
`Matrix…` and their `…Array` variants (`hutil/qt/info/nodeinfo.py`, `attrDataTypeColor`) —
the same colours VOP wires use. Tempting for the **Type** column, which is the direct
analogue.

Left alone because those names resolve from the **legacy** `.hcs` scheme, not the pluto
themes. They differ between UI Dark and UI Light (Float `#55D2C5` / `#317B73`, Int `#45B5FF`
/ `#286F9F`) and nothing else moves them: `setApplicationTheme` → `hcs.overrideFromPrefs`
republishes only `BackColor`, `TextColor`, `DisabledTextColor`, `SelectedTextBG`,
`SelectedTextFG`, `GroupLight` and the network/anim/syntax/usdview groups — `VopInOut*` is
not among them. Revisit if SideFX hooks those names up to the schemes.

Two things to know if it is: `hou.ui.colorFromName` and `hou.qt.toQColor` are GUI-only, so
that path cannot be exercised headlessly; and the Type column shows the provider's vocabulary
(`numeric[3]`, `topology[1]`, `paged primitive list`), not `hou.Attrib` qualifiers, so it
needs a mapping rather than reusing `attrDataTypeColor`.

The **scope** colours have no Houdini equivalent at all — public/private/groups plus the
view-only primitive_list is this panel's taxonomy, not a Houdini concept — so they stay
fixed, as does the legend mirroring them.

An older note here said the tree background had to be pinned or collapsing a branch flickered
(Houdini painting the empty area lighter than the rows). **Confirmed obsolete** — checked in
H22's new UI on 2026-08-08 with nothing pinned: no flicker. Do not reintroduce the pin.

Measured there, so the ownership of each surface is not guesswork: the top strip is
`pluto_bg` (45,45,45) and the tree rows are `pluto_field` (67,67,67) — both Houdini's — while
the grid canvas came back as our own literal and no longer does.

One surface is Houdini fighting itself rather than us: the area below the last row was filled
from `QAbstractItemView { background: rgb(@ListEntry2@) }` in `base.qss` (line 41). That is a
**legacy** `.hcs` colour, so it does not move with the new theme, and an app-level `background`
rule beats a viewport `backgroundRole` — measured (67,67,67) with the role set, the panel
colour once the viewport is transparent. Under `PlutoStyle` alone the style paints that area
itself, so a full-panel render cannot distinguish the two; the regression test drives the
mechanism directly instead.

Two consequences worth knowing:

* A style may fill each item's panel itself (`PE_PanelItemViewItem`), and H22's new UI does.
  Anything drawn *under* a row — the peer wash was, once — is silently erased. Draw over.
* The peer wash lifts a dark row and darkens a light one, so "lighter than the background" is
  not a property to assert or rely on; "different, and toward blue" is.

---

## 9. Behaviour details

**Refreshing.** Listen for node selection changes, parameter changes, input rewiring, cook
events, node deletion, and playbar frame changes. Coalesce these — several can arrive for one
user action — and do the actual refresh once, deferred to the idle loop rather than inside
the event callback.

**Never force a cook.** Read the geometry the node already produced. The panel is a viewer,
not a dependency.

**Emptying and restoring the selection must both reach the panel.** `onNodePathChanged`
fires on a change of node *path*, which covers neither: the pane tab continues to point at the
last node when the selection is emptied, and points at it still when it is re-selected. Compare
mode requires both transitions, and they must pass through the same entry point as any other
selection change so that Pause gates them.

*Recommended:* a `ChildSelectionChanged` callback on the enclosing network, added while pinned
and retained while the live node is None. `hou.ui.addSelectionCallback` carries the same
information but is available only in a graphical Houdini, which places it outside headless
test coverage.

**Errors.** If the provider raises (unresolvable path, node isn't a SOP, no cooked geometry),
show the message in the title bar and clear both sections. Don't let it propagate.

**Teardown.** Remove every callback when the panel closes. A deferred refresh may already be
queued and cannot be cancelled — it must check that the panel is still alive before touching
any widget, or it will run against destroyed Qt objects.

**Headless.** Import any graphical-only Houdini modules lazily, inside the function that
needs them, so the module still imports for testing outside a GUI session.

---

## 10. Performance targets

The page grid is the only performance-sensitive part. Geometry with a million pages is
normal.

* **Never expand per-slot data for the whole map.** Decode the provider's packed byte streams
  into per-page arrays and expand only the pages currently visible.
* **Draw the visible band, not the map.** Build one image covering the visible rows and blit
  it in a single operation. Cost should scale with the viewport, not with the page count.
* Target roughly 5 ms per frame in normal use, and no worse than ~25 ms in the pathological
  case of thousands of 1 px pages on screen.
* Cache decoded per-attribute page arrays; drop the cache when the node or output changes.

---

## 11. Acceptance checklist

- [ ] Selecting a SOP populates both sections; selecting nothing clears them cleanly.
- [ ] Category rows are always Attributes & Groups, Internal, Unaccounted, in that order.
- [ ] Toggling any scope leaves every total unchanged.
- [ ] Internal starts off; the other four scopes start on.
- [ ] The Nth bar matches the Nth toggled row after sorting by any column.
- [ ] A row toggled on one node stays on when another node with that attribute is selected.
- [ ] The selected row and any collapsed section come back after a node switch, a re-cook
      and an un-pause.
- [ ] Clicking a row emphasises its bar; hovering a bar outlines the row without changing the
      selection.
- [ ] The Pages column shows three slots, with dashes where the split is unmeasurable.
- [ ] Primitive-type rows start collapsed.
- [ ] Pause freezes selection, cook and timeline updates; un-pausing catches up.
- [ ] The instanced warning appears only when the report says so.
- [ ] Closing the panel removes all callbacks and produces no errors afterwards.
- [ ] Paused, selecting a node and pressing Reload reads *that* node and stays paused.
- [ ] Pin leaves the pause state alone, in both directions.
- [ ] Deselecting with a pin held shows the pinned node's own report, not the last live one's.
- [ ] Pinned with the same node selected, stepping a frame reads frame to frame.
- [ ] The page grid is hidden in every compare state and comes back with the previous
      splitter sizes on un-pin.
- [ ] The three categories keep their order under both sort directions.
- [ ] An attribute with no readable page table shows a dash in Δ Pages and Δ c/s/h, never a 0.
- [ ] An instanced side dashes Δ New and Δ Unique on every row, and Δ Total still reads.
- [ ] An added / removed / replaced row is styled across every column, and carries no
      row colour of its own.
- [ ] Δ Pages on an owner row equals its index map's page-count change.
- [ ] A row differing only in `data_id` is not drawn.
- [ ] Deleting the pinned node leaves the comparison working.
- [ ] Emptying the network selection falls back to the pinned node's report, and
      re-selecting that same node brings the comparison back.
- [ ] Pin sits left of Reload, and is disabled until there is a report to capture.
