---
feature_ids: []
related_features: []
topics: [product-ui, frontend, design-system, visual-review]
doc_kind: product-guidelines
created: 2026-08-02
---

# Product UI Rules

These rules apply to CodeTalk product screens: the workbench, task center, run
cockpit, workflow designer, settings, libraries, and other internal tools. They
do not apply to marketing pages unless a task explicitly says so.

The goal is task clarity. Do not optimize for apparent feature completeness or
for filling the viewport.

## Mandatory Workflow

Before implementing a new product screen or materially redesigning an existing
one, produce and use the following design brief:

- page purpose;
- primary user task;
- information hierarchy;
- removed elements;
- deferred elements;
- navigation split;
- progressive disclosure points.

After implementation, inspect browser screenshots at these viewports before
declaring the UI done:

- `1440x900`;
- `1280x800`;
- `390x844`.

The screenshot review must check:

- first-screen content allocation;
- wasted persistent header area;
- whether secondary controls occupy separate vertical rows;
- whether any component exists only because data is available;
- whether the layout uses cards as the default container;
- text fit, overlap, and mobile behavior.

Remove at least one unnecessary element, row, label, or container during review
unless the screenshot evidence shows there is nothing redundant to remove.

## Non-Negotiable Rules

1. Every screen must have one dominant user task.
2. Do not create an overview dashboard unless users genuinely need to compare
   multiple domains simultaneously.
3. Do not place a component on a page merely because the data exists.
4. Users are allowed to scroll. Never compress the interface merely to fit
   everything above the fold.
5. Empty space is intentional. Do not fill unused areas with cards, statistics,
   tips, activity feeds, announcements, or quick actions.
6. The persistent application header must not exceed `56px`.
7. Breadcrumbs, page descriptions, tabs, filters, and actions must not all
   occupy separate vertical rows.
8. Advanced filters and secondary actions should use progressive disclosure.
9. A card is not the default container. Use cards only when grouping creates
   meaningful boundaries.
10. Do not use visible in-app text to explain the application's features,
    visual styling, keyboard shortcuts, or how to use obvious controls.
11. Internal tools should feel quiet, dense enough for repeated work, and easy
    to scan. Do not style them like landing pages.
12. Optimize for task clarity, not apparent feature completeness.

## Design System Memory

When a product UI change establishes a reusable pattern, update this document
or a more specific design document with:

- the page type where the pattern applies;
- spacing and density choices;
- header and navigation behavior;
- card/container rules;
- primary and secondary action hierarchy;
- screenshot evidence used for acceptance.

Do not install or introduce a new design plugin, external MCP server, persistent
design subsystem, or repository dependency solely to improve aesthetics. Start
with this bounded brief, the existing frontend stack, and browser evidence.
