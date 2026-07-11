# Control Plane Design QA

## Source and comparison set

- Source: `docs/internal/ampule-chamber-improvement-plan.html#screens`
- Desktop reference viewport: 1440 × 1024
- Mobile verification viewport: 390 × 844
- Implementation captures:
  - `docs/internal/design-qa/wizard-desktop.png`
  - `docs/internal/design-qa/environment-desktop.png`
  - `docs/internal/design-qa/results-desktop.png`
  - `docs/internal/design-qa/wizard-mobile.png`

The HTML-native source blueprints and implementation captures were compared at
the same desktop geometry. The implemented hierarchy preserves the blueprint's
top context bar, restrained navigation rail, progressive setup steps, paired
environment modes, explicit preflight state, dense run tabs, prioritized result
area, and compact readiness panel. The implementation uses the reference's
warm white and pale green surfaces, dark green action color, fine borders,
small status pills, conservative radius, and compact technical typography.

## Required surfaces

| Surface | Result |
| --- | --- |
| Guided target, environment, exercise, and review flow | passed |
| Local, Kubernetes deploy, and Kubernetes attach modes | passed |
| Capability/preflight state | passed |
| Traffic and attach-fault templates | passed |
| Plan review before execution | passed |
| Live execution and cancel-to-cleanup action | passed |
| Result overview, timeline, findings, evidence, config, agents | passed |
| HTML, Markdown, and JSON report export | passed |
| Compatible-run comparison | passed |
| Desktop and mobile responsive behavior | passed |

## Interaction and accessibility checks

- Wizard `Continue` advances only after current required fields validate.
- Environment radio selection reveals Kubernetes-only fields and updates the
  selected visual state.
- Native selects remain keyboard-operable for runtime, traffic, fault, and
  agent choices.
- Results evidence tab navigation was exercised in the browser and exposed four
  digest-registered evidence rows with valid download links.
- A 390 px viewport reported `scrollWidth == clientWidth`; no horizontal page
  overflow was present.
- Skip link, landmark navigation, labels, focus-visible outlines, reduced-motion
  handling, status text, and 42 px minimum button heights are present.
- Browser console warning/error log was empty on setup and evidence states.

## QA history

1. Initial desktop setup render matched the reference structure but used a
   decorative letter tile in the wordmark.
2. Kubernetes selection and conditional fields were exercised; all installed
   capabilities displayed as ready.
3. The decorative tile and empty-state symbol were removed so the UI does not
   substitute text/CSS drawings for product assets.
4. Final desktop setup, desktop results, mobile setup, result-tab navigation,
   overflow, and console checks passed.

final result: passed
