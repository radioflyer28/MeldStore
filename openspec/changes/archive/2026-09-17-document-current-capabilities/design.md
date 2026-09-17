# Design

## Context

See `proposal.md` for motivation. The MVP documentation contains detailed API,
failure, qualification, and operational evidence. The OpenSpec baseline summarizes
only current generic behavior that future changes must preserve or intentionally amend.

## Goals / Non-Goals

**Goals:**

- Establish a compact behavioral source of truth for the released MVP.
- Keep storage, metadata, handler, and lifecycle responsibilities separately discoverable.
- Preserve genericity and qualification boundaries.

**Non-Goals:**

- Add a consumer domain model, caching features, or new backend support.
- Replace operational guides, release notes, or measured verification reports.
- Treat MeldDB implementation details as MeldStore behavior.

## Decisions

- Use six flat capabilities matching application workflows rather than source modules.
- Record baseline requirements through a documentation-only change and archive it into main specs.
- Put only the MeldDB/MeldStore responsibility boundary in the shared store; local payload contracts remain here.
- State qualification separately from guarantees so passing on one compatible service cannot become a universal claim.

## Risks / Trade-offs

- **Summaries omit codec and backend edge cases** -> Keep detailed guides as supporting evidence and use future deltas for behavior changes.
- **Cross-repo facts can be duplicated** -> Reference the shared store and keep local specs focused on MeldStore-owned behavior.
- **Baseline may fossilize implementation choices** -> Specify observable outcomes and boundaries, not internal classes or object-key algorithms.

## Migration Plan

Validate and archive this documentation-only change to create the six main specs.
No catalog, payload, package, or runtime migration occurs.
