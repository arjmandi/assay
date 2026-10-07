# assay_grid, the frame-world extra

Everything a world whose observation is a grid needs beyond the kernel's frame
encoding. The kernel (`src/assay`) keeps the encoding because the journal
format has it: `frames` as hex rows, `n_frames`, and the frame branch of
`normalize_observation`. This package holds the rest:

| module | what it is |
|---|---|
| `perception.py` | connected components, repeated shapes, lattice inference, line graphs, frame deltas, motion traces, the transition story, the scene dossier |
| `render.py` | the palette, one PNG per event under `.assay/images/`, the observation hash the rules tier pins plans to, the frame form of the history line. The one place pillow is imported |
| `claims.py` | the four grid claim forms (`cell`, `move`, `vanish`, `region`) and the frame grader |
| `views.py` | the frame halves of status, result, inspect, view and export: board text, diffs, scene summary, animation, click candidates, the RULES and PLAN lines |
| `rules.py` | the executable-rules tier: `rules.py` contract, replay, A* search |
| `solve.py` | `assay rules help, init, replay, solve` and the solve-plan executor behind `assay commit @.assay/plan.json` |
| `analysis.py` | the grid namespace of `assay python` |
| `legacy.py` | the numbered-action vocabulary of runs without a registry (`ACTION1..7`, `ACTION6:x,y`), kept undocumented for the run directories that used it (owner decision O2) |
| `__init__.py` | `KIND`, the one object the kernel talks to |

## How it is selected

By observation shape, never by configuration. `assay.extras.kind_for(event)`
returns `KIND` when the event has `frames` and `None` otherwise, importing this
package lazily. Registries are pinned per run and the published run
directories carry no key for this, so they keep rendering, inspecting and
auditing unchanged. A dict world never imports this package or pillow.

## What the ARC-AGI-3 adapter needs from it

Rendering, the scene dossier, the inspect views and the grid namespace of
`assay python`. Nothing else: the ARC runs were registry runs, so the grid
claim forms were refused and the rules tier was unavailable to them (the
general world model, `assay model`, is the registry counterpart).

## The rule on claim forms

On a registry run the grid claim forms are refused before any spend, which is
the rule every published registry journal was recorded under. Whether
frame-world registry runs should admit them is owner decision O1 and is not
made here.

## Extension points

A second observation kind implements the same names as `FrameKind` (listed in
`assay.extras.ObservationKind`) and is returned by `assay.extras.kind_for`.
