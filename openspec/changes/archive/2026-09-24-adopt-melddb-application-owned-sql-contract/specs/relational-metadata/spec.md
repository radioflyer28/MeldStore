# Spec Delta

## ADDED Requirements

### Requirement: MeldStore SQL remains application-owned under MeldDB
When the MeldDB adapter is selected, the system SHALL retain ownership of MeldStore catalog tables, application tables, their migrations, and their constraints as ordinary application-owned SQL. It MUST NOT register, adopt, or depend on those objects as MeldDB-managed document, graph, or migration state, and the same database MUST remain usable through the direct SQLite adapter without conversion.

#### Scenario: MeldDB inspects a MeldStore catalog
- **WHEN** a caller inspects an installed MeldStore catalog through MeldDB's public inspection interface
- **THEN** MeldStore and application tables are classified as external SQL objects and no MeldDB-managed objects or migrations are reported for them

#### Scenario: Application constraint rejects shared publication
- **WHEN** a MeldStore metadata write and an application-owned relational write share one transaction and an application constraint rejects the work
- **THEN** both sets of SQL changes roll back and no blob is reported ready

#### Scenario: Catalog reopens without adoption
- **WHEN** a catalog written through the MeldDB adapter is reopened through the direct SQLite adapter, or vice versa
- **THEN** MeldStore tables, application tables, foreign keys, identifiers, and data remain usable without an adoption or migration step
