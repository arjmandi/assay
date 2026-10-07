# assay_grid, the frame-world extra

Everything a world whose observation is a grid needs beyond the kernel's frame
encoding. The kernel (`src/assay`) keeps the encoding because the journal
format has it: `frames` as hex rows, `n_frames`, and the frame branch of
`normalize_observation`. This package holds the rest:

| module | what it is |
|---|---|
| `perception.py` | connected components, repeated shapes, lattice inference, line graphs, frame deltas, motion traces, the transition story, the scene dossier |
| `render.py` | the palette, one PNG per event under `.assay/images/`, the frame form of the history line. The one place pillow is imported |
| `claims.py` | the four grid claim forms (`cell`, `move`, `vanish`, `region`) and the frame grader |
| `views.py` | the frame halves of status, result, inspect, view and export: board text, diffs, scene summary, animation, click candidates, the advertised-action line |
| `analysis.py` | the grid namespace of `assay python` |
| `__init__.py` | `KIND`, the one object the kernel talks to |

## How it is selected

By observation shape, never by configuration. `assay.extras.kind_for(event)`
returns `KIND` when the event has `frames` and `None` otherwise, importing this
package lazily. Registries are pinned per run and the published run
directories carry no key for this, so they keep rendering, inspecting and
auditing unchanged. A dict world never imports this package or pillow.

## What the ARC-AGI-3 adapter needs from it

Rendering, the scene dossier, the inspect views and the grid namespace of
`assay python`. Nothing else: the grid claim forms were refused on the ARC
runs, and the general world model (`assay model`) was their model tier.

## The rule on claim forms

The grid claim forms are recognized and refused by name before any spend,
which is the rule every published journal was recorded under. Whether frame
worlds should admit them is owner decision O1 and is not made here.

## Extension points

A second observation kind implements the same names as `FrameKind` (listed in
`assay.extras.ObservationKind`) and is returned by `assay.extras.kind_for`.
