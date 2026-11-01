# DephGraph — Phase 3b: Constraints and Relational Algebra

This completes **Unit II** of the syllabus for the DephGraph schema:

1. **Relational model constraints**: domain, key, entity-integrity and referential-integrity constraints
2. **Update operations and constraint violations**: what happens when an insert, delete or update breaks a rule
3. **Relational algebra**: the project's main questions written with σ, π, ⋈, ρ, ∪, ∩, −, × and ÷

All examples use the 23 relations and the sample rows from Phase 2
([`../relational-model/`](../relational-model/README.md#the-tables-with-sample-rows)), so every
result below can be checked by hand.

---

# Part 1 — Relational model constraints

## 1.1 Domain constraints

Every attribute takes values from a fixed domain (data type plus allowed values).

| Attribute | Domain |
|---|---|
| SERVICE.criticality | {LOW, MEDIUM, HIGH, CRITICAL} |
| DEPLOYMENT.environment | {DEV, STAGING, PROD} |
| PACKAGE.ecosystem | {npm, PyPI, Maven, Go, crates.io, NuGet} |
| VULNERABILITY.severity | {LOW, MEDIUM, HIGH, CRITICAL} |
| VULNERABILITY.cvss_score | decimal, 0.0 ≤ cvss_score ≤ 10.0 |
| DEPENDS_ON.scope | {runtime, dev, optional, peer} |
| MAINTAINED_BY.role | {OWNER, COMMITTER, CONTRIBUTOR} |
| REMEDIATION_PLAN.status | {DRAFT, APPROVED, IN_PROGRESS, DONE} |
| REMEDIATION_ACTION.action_type | {UPGRADE, REMOVE, CONSOLIDATE, REPLACE} |
| PR_REVIEW.filter_stage_exit | {STAGE_1, STAGE_2, STAGE_3} |
| PR_RISK_VERDICT.verdict | {RISK, WARN, APPROVE} |
| OBSERVES.exec_count | integer ≥ 0 |
| RESOLVES_TO.depth | integer ≥ 0 (0 = direct dependency) |
| `is_*` attributes | {TRUE, FALSE} |

The enumerated domains are enforced in `schema.sql` by `ENUM` types and the CVSS range by a `CHECK`.
The two "≥ 0" rules are part of the design but are not yet enforced by a `CHECK` in `schema.sql`.

Some rules involve more than one attribute of the same row. These are written as `CHECK` constraints
in `schema.sql`:

| Constraint | Rule |
|---|---|
| chk_scan_window | SCAN_RUN.ended_at is NULL or ≥ started_at |
| chk_pr_dates | PR_REVIEW.merged_at is NULL or ≥ opened_at |
| chk_no_self_dep | a package version cannot depend on itself in DEPENDS_ON |

## 1.2 Key constraints

No two tuples of a relation may have the same value for a candidate key.

| Relation | Primary key | Other candidate keys (UNIQUE) |
|---|---|---|
| TEAM | team_id | team_name |
| SERVICE | service_id | service_name |
| PACKAGE | package_id | (package_name, ecosystem) |
| LICENSE | license_id | spdx_id |
| MAINTAINER | maintainer_id | handle |
| VULNERABILITY | vuln_id | osv_id |
| weak entities and relationship relations | composite keys, listed in Phase 2 | — |

## 1.3 Entity integrity

No attribute of a primary key may be NULL. For a weak entity this covers **both** parts of the key:
a DEPLOYMENT needs both `service_id` and `deploy_seq`, and a PR_RISK_VERDICT needs all three of
`service_id`, `pr_number` and `dep_name`.

## 1.4 Referential integrity

A foreign-key value must either match an existing primary-key value in the referenced relation or,
where allowed, be NULL. DephGraph has 26 foreign keys (full list in
[Phase 2](../relational-model/README.md#referential-integrity-constraints-all-foreign-keys)).

Only two are nullable, both because the relationship has partial participation:
`PACKAGE.cluster_id` (GROUPED_INTO) and `PACKAGE_VERSION.license_id` (LICENSED_UNDER).

What happens to referencing rows when a referenced row is deleted:

| Action | Used for | Reason |
|---|---|---|
| **RESTRICT** (reject the delete) | SERVICE.team_id, REMEDIATION_PLAN.team_id | a team that still owns services or plans must not disappear silently |
| **SET NULL** | PACKAGE.cluster_id, PACKAGE_VERSION.license_id | the participation is partial, so the row is still valid without the link |
| **CASCADE** (delete the referencing rows too) | every other FK, including all weak entities and relationship relations | a weak entity or a relationship row has no meaning without its owner |

---

# Part 2 — Update operations and constraint violations

Each operation is applied to the Phase 2 sample data.

## 2.1 Insert

| # | Operation | Constraint violated | DBMS action |
|---|---|---|---|
| I1 | INSERT INTO SERVICE ⟨12, 'billing-svc', 'HIGH', team_id = 9⟩ | **Referential integrity**: there is no TEAM 9 | rejected |
| I2 | INSERT INTO DEPLOYMENT ⟨service_id = NULL, 3, 'PROD', …⟩ | **Entity integrity**: part of the primary key is NULL | rejected |
| I3 | INSERT INTO TEAM ⟨3, 'Payments', …⟩ | **Key constraint**: team_name 'Payments' already exists | rejected |
| I4 | INSERT INTO VULNERABILITY ⟨902, 'GHSA-xxxx', 'HIGH', 11.5⟩ | **Domain constraint**: cvss_score must be ≤ 10.0 | rejected |
| I5 | INSERT INTO DEPENDS_ON ⟨100, '2.14.1', 100, '2.14.1', 'runtime', TRUE⟩ | **Semantic constraint** chk_no_self_dep | rejected |
| I6 | INSERT INTO PACKAGE ⟨103, 'gson', 'Maven', cluster_id = NULL⟩ | none: cluster_id is allowed to be NULL | accepted |

## 2.2 Delete

Delete can only violate referential integrity. The ON DELETE action decides the outcome.

| # | Operation | Effect |
|---|---|---|
| D1 | DELETE FROM TEAM WHERE team_id = 1 | **Rejected (RESTRICT)**: SERVICE 10 and REMEDIATION_PLAN 50 still reference team 1 |
| D2 | DELETE FROM SERVICE WHERE service_id = 10 | **Cascades**: also removes 2 DEPLOYMENT rows, then the 3 RESOLVES_TO and 2 OBSERVES rows of those deployments, 1 PR_REVIEW row and its 2 PR_RISK_VERDICT rows, and 1 WAIVED_FOR row: 11 rows in total |
| D3 | DELETE FROM LICENSE WHERE license_id = 2 | **Set NULL**: PACKAGE_VERSION (101, '1.2.3') keeps existing with license_id = NULL |

## 2.3 Update

An update is a delete followed by an insert, so it can violate any of the above.

| # | Operation | Effect |
|---|---|---|
| U1 | UPDATE SERVICE SET team_id = 9 WHERE service_id = 11 | **Rejected**: referential integrity, there is no TEAM 9 |
| U2 | UPDATE PACKAGE_VERSION SET version_string = '2.14.2' WHERE package_id = 100 AND version_string = '2.14.1' | **Rejected**: this primary key is referenced by RESOLVES_TO, DEPENDS_ON, AFFECTS and OBSERVES, and no ON UPDATE CASCADE is defined |
| U3 | UPDATE VULNERABILITY SET cvss_score = 9.8 WHERE vuln_id = 901 | **Accepted**: non-key attribute, value inside its domain |

---

# Part 3 — Relational algebra

## Operators used

| Symbol | Operation | Type |
|---|---|---|
| σ<sub>condition</sub>(R) | SELECT: keep rows that satisfy the condition | unary |
| π<sub>attributes</sub>(R) | PROJECT: keep only these columns (duplicates removed) | unary |
| ρ<sub>S(B1,…,Bn)</sub>(R) | RENAME the relation and/or its attributes | unary |
| R ∪ S, R ∩ S, R − S | UNION, INTERSECTION, DIFFERENCE (R and S must be union-compatible) | set theory |
| R × S | CARTESIAN PRODUCT | set theory |
| R ⋈ S | NATURAL JOIN on attributes with the same name | binary |
| R ÷ S | DIVISION: values of R related to **every** tuple of S | binary |
| T ← expression | assign a result to a temporary relation | — |

**Why natural joins work cleanly here:** in Phase 2 every foreign key kept the same name as the key it
references (`service_id`, `package_id`, …). So `R ⋈ S` joins exactly on the FK = PK pair.
The one exception is DEPENDS_ON, whose role-named columns (`dependent_*`, `dependency_*`) need
renaming with ρ before joining.

---

## Q1 — Blast radius: which services run a version affected by Log4Shell?

```
V    ← π vuln_id ( σ osv_id = 'GHSA-jfh8-c2jp-5v3q' (VULNERABILITY) )
AV   ← π package_id, version_string ( AFFECTS ⋈ V )
PROD ← σ environment = 'PROD' ∧ is_active = TRUE (DEPLOYMENT)
S    ← π service_id ( PROD ⋈ RESOLVES_TO ⋈ AV )
Q1   ← π service_name ( SERVICE ⋈ S )
```

**On the sample data:** V = {900} → AV = {(100, 2.14.1)} → S = {10} → **Q1 = {payments-svc}**.

### Why RESOLVES_TO is needed: the limit of relational algebra

RESOLVES_TO already lists every package version a deployment reaches, at any depth. Without it the
query would have to follow DEPENDS_ON edges itself, one join per level:

```
D      ← ρ D(pkg, ver, dep_pkg, dep_ver) ( π dependent_package_id, dependent_version,
                                              dependency_package_id, dependency_version (DEPENDS_ON) )
LEVEL0 ← ρ (dep_pkg, dep_ver) (AV)                         -- the affected version itself
LEVEL1 ← π pkg, ver ( D ⋈ LEVEL0 )                        -- versions that depend on it directly
LEVEL2 ← π pkg, ver ( D ⋈ ρ (dep_pkg, dep_ver) (LEVEL1) )  -- one level further up
…
```

On the sample data LEVEL1 = {(102, 2.5.0)} (spring-boot-starter-log4j2) and LEVEL2 = ∅.

Each extra level needs one more join, and a dependency chain can be any length. **Basic relational
algebra cannot express transitive closure** (a query of unbounded depth). That is why DephGraph stores
the closure in RESOLVES_TO, and why the SQL version of this query uses a recursive CTE
(`WITH RECURSIVE` in `schema.sql`, Q1).

---

## Q2 — Prune: which package versions are deployed but never executed?

Uses **set difference**.

```
RES  ← π service_id, deploy_seq, package_id, version_string ( RESOLVES_TO )
LIVE ← π service_id, deploy_seq, package_id, version_string ( σ exec_count > 0 (OBSERVES) )
DEAD ← RES − LIVE
Q2   ← π service_id, package_name, version_string ( DEAD ⋈ PACKAGE )
```

**On the sample data:**
- RES = {(10,1,102,2.5.0), (10,1,100,2.14.1), (10,1,101,1.2.3)}
- LIVE = {(10,1,100,2.14.1)}: log4j-core ran 1840 times; logback-classic ran 0 times
- **Q2 = {(10, spring-boot-starter-log4j2, 2.5.0), (10, logback-classic, 1.2.3)}**

spring-boot-starter-log4j2 is a "starter": a bundle with no code of its own, so it is never seen
executing. logback-classic is shipped but unused, so it can be removed.

---

## Q3 — Minimum remediation: which upgrade fixes the most?

Counting needs **aggregate functions**, an extension to basic relational algebra (written ℱ in
Elmasri & Navathe; γ in other textbooks): `grouping-attributes ℱ functions (R)`.

```
E   ← π service_id, package_id, version_string, vuln_id ( PROD ⋈ RESOLVES_TO ⋈ AFFECTS )
SVC ← package_id, version_string ℱ COUNT service_id ( π service_id, package_id, version_string (E) )
VUL ← package_id, version_string ℱ COUNT vuln_id    ( π package_id, version_string, vuln_id (E) )
Q3  ← SVC ⋈ VUL
```

**On the sample data:** E = {(10,100,2.14.1,900), (10,100,2.14.1,901)} →
**Q3 = {(100, 2.14.1, services = 1, vulns = 2)}**: upgrading log4j-core 2.14.1 fixes 1 service and
clears 2 vulnerabilities.

This gives one round of the greedy set-cover algorithm: pick the row with the highest count, mark its
vulnerabilities as covered, and run again on what is left. The loop runs in the application; each
round is the query above.

---

## Q4 — Licence propagation: which services ship copyleft code?

```
CL ← σ is_copyleft = TRUE (LICENSE)
Q4 ← π service_name, package_name, version_string, spdx_id
        ( CL ⋈ PACKAGE_VERSION ⋈ RESOLVES_TO ⋈ SERVICE ⋈ PACKAGE )
```

**On the sample data:** CL = {LGPL-2.1-only} → PACKAGE_VERSION (101, 1.2.3) →
**Q4 = {(payments-svc, logback-classic, 1.2.3, LGPL-2.1-only)}**.

---

## Q5 — Consolidate: which services use two packages that do the same job?

Uses a **self-join** with ρ, so no aggregation is needed.

```
SP  ← π service_id, package_id, cluster_id ( RESOLVES_TO ⋈ σ cluster_id IS NOT NULL (PACKAGE) )
SP2 ← ρ SP2(service_id, package_id2, cluster_id) (SP)
RED ← π service_id, cluster_id ( σ package_id < package_id2 ( SP ⋈ SP2 ) )
Q5  ← π service_name, cluster_label ( RED ⋈ SERVICE ⋈ SEMANTIC_CLUSTER )
```

The natural join SP ⋈ SP2 matches rows with the same service and the same cluster;
`package_id < package_id2` keeps pairs of *different* packages and lists each pair once.

**On the sample data:** SP = {(10,100,3), (10,101,3)} (package 102 has no cluster) →
the pair (100, 101) → **Q5 = {(payments-svc, logging)}**: log4j-core and logback-classic are both
logging libraries in the same service.

---

## Q6 — Division: which maintainers maintain *every* package in the logging cluster?

Uses **division**, the operator for "for all" questions.

```
LOG ← π package_id ( σ cluster_label = 'logging' (SEMANTIC_CLUSTER) ⋈ PACKAGE )
MP  ← π maintainer_id, package_id ( MAINTAINED_BY )
Q6  ← π handle ( (MP ÷ LOG) ⋈ MAINTAINER )
```

Division written with basic operators, to show how it works
(with A = maintainer_id, R = MP, S = LOG):

```
R ÷ S = π A (R)  −  π A ( (π A (R) × S) − R )
```

**On the sample data:**

| Step | Value |
|---|---|
| LOG | {100, 101} |
| R = MP | {(200, 100), (201, 101)} |
| π A (R) | {200, 201} |
| π A (R) × S | {(200,100), (200,101), (201,100), (201,101)}: every maintainer paired with every logging package |
| (π A (R) × S) − R | {(200,101), (201,100)}: the pairs that are missing |
| π A of that | {200, 201}: maintainers missing at least one package |
| R ÷ S | {200, 201} − {200, 201} = **∅** |

**Q6 = ∅**: no single maintainer covers the whole logging cluster.
If MAINTAINED_BY also had the row (101, 200), the missing pairs would be {(201, 100)} only, so
R ÷ S = {200} and **Q6 = {mdev}**.

---

## Q7 — Union, intersection and difference: waived and open exposure

```
AFF    ← π vuln_id, service_id ( RESOLVES_TO ⋈ AFFECTS )
WAIVED ← π vuln_id, service_id ( σ expires_on ≥ '2026-11-02' (WAIVED_FOR) )

Q7a ← AFF ∩ WAIVED      -- exposures the team has accepted for now
Q7b ← AFF − WAIVED      -- exposures that still need fixing
```

AFF and WAIVED are **union-compatible**: same number of attributes, with the same domains.

**On the sample data:** AFF = {(900, 10), (901, 10)}, WAIVED = {(901, 10)} →
**Q7a = {(901, 10)}** and **Q7b = {(900, 10)}**: payments-svc has accepted GHSA-7rjr-3q55-vv33 until
30 Nov 2026, but Log4Shell (GHSA-jfh8-c2jp-5v3q) is still open.

**Union:** packages that need a review because they are copyleft *or* vulnerable:

```
Q7c ← π package_id ( σ is_copyleft = TRUE (LICENSE) ⋈ PACKAGE_VERSION )  ∪  π package_id ( AFFECTS )
```

**On the sample data:** {101} ∪ {100} = **{100, 101}** (logback-classic and log4j-core).

---

## Summary

| Query | Question | Operators shown |
|---|---|---|
| Q1 | Blast radius | σ, π, ⋈, ρ; why transitive closure needs RESOLVES_TO |
| Q2 | Prune unused packages | − (difference) |
| Q3 | Minimum remediation | ℱ aggregate functions |
| Q4 | Licence propagation | multi-way ⋈ |
| Q5 | Redundant packages | self-join with ρ |
| Q6 | Maintainers covering a cluster | ÷ (division), also × |
| Q7 | Waived and open exposure | ∩, −, ∪ |

The same questions are written in SQL at the end of [`../er-diagram/schema.sql`](../er-diagram/schema.sql).
