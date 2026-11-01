# DephGraph — Phase 3a: Functional Dependencies and Normalization

Phase 2 turned the ER model into 23 relations ([`../relational-model/`](../relational-model/)).
This phase **proves those relations are well designed**, using the tools from Unit III
(*Relational Database Design*):

1. functional dependencies (FDs) and Armstrong's inference rules
2. attribute closure and candidate keys
3. minimal cover and equivalence of FD sets
4. normal forms: 1NF, 2NF, 3NF and BCNF
5. properties of decompositions: lossless join and dependency preservation

Part A works through one complete example, normalizing a single "flat" table step by step until it
becomes our tables. Part B checks every one of the 23 relations.

---

## Notation

| Symbol | Meaning |
|---|---|
| `X → Y` | X functionally determines Y: two rows that agree on X must agree on Y |
| `X⁺` | Closure of X: every attribute X determines under the given FDs |
| <ins>underlined</ins> | Primary key |
| `R1 ∩ R2` | Attributes two relations share after a decomposition |

**Armstrong's inference rules** (used in every closure below):

| Rule | Statement |
|---|---|
| Reflexivity | If Y ⊆ X then X → Y |
| Augmentation | If X → Y then XZ → YZ |
| Transitivity | If X → Y and Y → Z then X → Z |
| Union *(derived)* | If X → Y and X → Z then X → YZ |
| Decomposition *(derived)* | If X → YZ then X → Y and X → Z |
| Pseudo-transitivity *(derived)* | If X → Y and WY → Z then WX → Z |

---

# Part A — Worked example: normalizing a flat scan report

## A.1 The starting point: one big table

A dependency scanner naturally produces one wide report: *for every service, every package version
it uses, and every vulnerability in that version*. Suppose we stored that report as a single table.

**SCAN_REPORT** (unnormalized):

| service_id | service_name | team_id | team_name | package_id | package_name | aliases | version_string | license_id | spdx_id | is_copyleft | vuln_id | osv_id | cvss_score | fixed_in |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 10 | payments-svc | 1 | Payments | 100 | log4j-core | {log4j2, org.apache.logging.log4j:log4j-core} | 2.14.1 | 1 | Apache-2.0 | FALSE | 900 | GHSA-jfh8-c2jp-5v3q | 10.0 | 2.15.0 |
| 10 | payments-svc | 1 | Payments | 100 | log4j-core | {log4j2, org.apache.logging.log4j:log4j-core} | 2.14.1 | 1 | Apache-2.0 | FALSE | 901 | GHSA-7rjr-3q55-vv33 | 9.0 | 2.16.0 |
| 11 | auth-svc | 2 | Platform | 100 | log4j-core | {log4j2, org.apache.logging.log4j:log4j-core} | 2.14.1 | 1 | Apache-2.0 | FALSE | 900 | GHSA-jfh8-c2jp-5v3q | 10.0 | 2.15.0 |

The same data as Phase 2's sample rows, with one extra service (auth-svc) that also uses log4j-core.

### Anomalies in this table

| Anomaly | Example |
|---|---|
| **Update** | Renaming the team "Payments" means changing every row of every service it owns. Miss one and the database contradicts itself. |
| **Insertion** | `logback-classic 1.2.3` is used by payments-svc but has no vulnerability. It cannot be stored without putting NULL into `vuln_id`, which is part of the key. A newly published CVE that no service uses yet cannot be stored either. |
| **Deletion** | If auth-svc stops using log4j-core, deleting its row also deletes the fact that GHSA-jfh8-c2jp-5v3q is fixed in 2.15.0, if that was the last row mentioning it. |
| **Redundancy** | "log4j-core", its aliases, its licence and the CVSS score 10.0 are repeated on every row. |

## A.2 Functional dependencies

From the meaning of the data, collected as stated by the requirements (deliberately including some
redundant ones, as real requirement lists do):

| # | FD | Why it holds |
|---|---|---|
| F1 | service_id → service_name, team_id, team_name | a service has one name and one owning team |
| F2 | team_id → team_name | |
| F3 | package_id → package_name | |
| F4 | package_id, version_string → package_name, license_id | a released version carries one licence |
| F5 | license_id → spdx_id, is_copyleft | |
| F6 | spdx_id → license_id | SPDX identifiers are unique |
| F7 | package_id, version_string → spdx_id | |
| F8 | vuln_id → osv_id, cvss_score | |
| F9 | osv_id → vuln_id | OSV identifiers are unique |
| F10 | package_id, version_string, vuln_id → fixed_in | the fix for this CVE on this version's release line |

`aliases` is multivalued, so it is not part of any FD. It is dealt with in 1NF.

## A.3 Minimal cover

A minimal cover is an equivalent set of FDs with (1) a single attribute on every right side,
(2) no extraneous attribute on any left side and (3) no redundant FD.

**Step 1: single attribute on the right** (decomposition rule)

F1 → `service_id → service_name`, `service_id → team_id`, `service_id → team_name`
F4 → `package_id, version_string → package_name`, `package_id, version_string → license_id`
F5 → `license_id → spdx_id`, `license_id → is_copyleft`
F8 → `vuln_id → osv_id`, `vuln_id → cvss_score`
The rest already have one attribute on the right.

**Step 2: remove extraneous left-side attributes**

For each FD with more than one attribute on the left, test whether a smaller left side still
determines the right side, by computing the closure of the smaller set.

| FD | Test | Result |
|---|---|---|
| package_id, version_string → package_name | {package_id}⁺ = {package_id, package_name} contains package_name | `version_string` is extraneous: becomes package_id → package_name |
| package_id, version_string → license_id | {package_id}⁺ = {package_id, package_name}; {version_string}⁺ = {version_string} | neither contains license_id: keep |
| package_id, version_string → spdx_id | same closures as above | keep (for now) |
| package_id, version_string, vuln_id → fixed_in | {package_id, version_string}⁺, {package_id, vuln_id}⁺ and {version_string, vuln_id}⁺: none contains fixed_in | keep |

**Step 3: remove redundant FDs**

An FD `X → A` is redundant if A is still in X⁺ after removing that FD.

| FD | Closure without it | Result |
|---|---|---|
| service_id → team_name | {service_id}⁺ = {service_id, service_name, team_id, **team_name**} (via team_id → team_name, transitivity) | redundant: remove |
| package_id → package_name (from Step 2) | duplicate of F3 | remove the duplicate |
| package_id, version_string → spdx_id | {package_id, version_string}⁺ = {…, license_id, **spdx_id**, is_copyleft} (via license_id → spdx_id) | redundant: remove |
| every other FD | its right side is not reachable without it | keep |

**Minimal cover G:**

```
G1   service_id                         → service_name
G2   service_id                         → team_id
G3   team_id                            → team_name
G4   package_id                         → package_name
G5   package_id, version_string         → license_id
G6   license_id                         → spdx_id
G7   license_id                         → is_copyleft
G8   spdx_id                            → license_id
G9   vuln_id                            → osv_id
G10  vuln_id                            → cvss_score
G11  osv_id                             → vuln_id
G12  package_id, version_string, vuln_id → fixed_in
```

**Equivalence of F and G.** Two FD sets are equivalent when each is contained in the closure of the
other.
- G ⊆ F⁺: every FD in G is either in F or obtained from F by the decomposition rule.
- F ⊆ G⁺: the only FDs of F missing from G are `service_id → team_name` (G2 + G3 by transitivity),
  `package_id, version_string → package_name` (G4 by augmentation) and
  `package_id, version_string → spdx_id` (G5 + G6 by transitivity).

So F ≡ G, and the rest of the design uses G.

## A.4 Candidate keys

Attributes that never appear on the right side of G must be in every key:
`service_id`, `package_id`, `version_string`. Neither `vuln_id` nor `osv_id` is determined by the
others, so one of them must be added.

```
{service_id, package_id, version_string, vuln_id}⁺
  = service_id, service_name, team_id, team_name        (G1, G2, G3)
  + package_id, package_name                            (G4)
  + version_string, license_id, spdx_id, is_copyleft    (G5, G6, G7)
  + vuln_id, osv_id, cvss_score                         (G9, G10)
  + fixed_in                                            (G12)
  = all 14 attributes ✓
```

No proper subset reaches all attributes, so this is a candidate key. Replacing `vuln_id` by
`osv_id` gives the second candidate key (because of G11).

- **Primary key:** {service_id, package_id, version_string, vuln_id}
- **Prime attributes** (part of some candidate key): service_id, package_id, version_string, vuln_id, osv_id

## A.5 First normal form (1NF)

**Rule:** every attribute holds a single atomic value; no repeating groups or lists.

`aliases` holds a set such as `{log4j2, org.apache.logging.log4j:log4j-core}`, so SCAN_REPORT is
**not in 1NF**. Move the multivalued attribute into its own relation, keyed by the package and the
value:

- **PACKAGE_ALIAS**(<ins>package_id</ins>, <ins>alias</ins>)
- **SCAN_REPORT_1NF** = SCAN_REPORT without `aliases` (14 attributes, key unchanged)

## A.6 Second normal form (2NF)

**Rule:** in 1NF, and no non-prime attribute depends on only *part* of a candidate key
(no partial dependencies).

The key has four attributes, and several attributes depend on only part of it:

| Partial dependency | Depends only on |
|---|---|
| service_name, team_id, team_name | service_id |
| package_name | package_id |
| license_id, spdx_id, is_copyleft | package_id, version_string |
| cvss_score | vuln_id (osv_id is prime, so it does not count) |

Even `fixed_in` is partial: it depends on `package_id, version_string, vuln_id`, which leaves out
`service_id`. The fix for a CVE does not change with the service that uses the version.

So SCAN_REPORT_1NF is **not in 2NF**. Decompose: each partial dependency gets its own relation,
keyed by the part of the key it depends on.

| New relation | Attributes |
|---|---|
| R1 | <ins>service_id</ins>, service_name, team_id, team_name |
| R2 | <ins>package_id</ins>, package_name |
| R3 | <ins>package_id</ins>, <ins>version_string</ins>, license_id, spdx_id, is_copyleft |
| R4 | <ins>vuln_id</ins>, osv_id, cvss_score |
| R5 | <ins>package_id</ins>, <ins>version_string</ins>, <ins>vuln_id</ins>, fixed_in |
| R6 | <ins>service_id</ins>, <ins>package_id</ins>, <ins>version_string</ins> |

R6 records which service uses which package version. It drops `vuln_id`, because which
vulnerabilities a version has is already recorded once in R5. The original rows can be rebuilt as
R6 ⋈ R5. This removal relies on a *multivalued* dependency (a package version's vulnerabilities do
not depend on which service uses it), which is the subject of 4NF, beyond this syllabus. Dropping it
also fixes the insertion anomaly: logback-classic can now be stored in R6 with no vulnerability.

Every relation is now in 2NF: each non-prime attribute depends on the whole key of its relation.

## A.7 Third normal form (3NF)

**Rule:** in 2NF, and no non-prime attribute depends transitively on a key. Formally, for every
non-trivial FD `X → A`, either X is a superkey or A is prime.

| Relation | Transitive dependency | Violates 3NF? |
|---|---|---|
| R1 | service_id → team_id → team_name | **yes**: team_id is not a key of R1 and team_name is not prime |
| R2 | none | no |
| R3 | (package_id, version_string) → license_id → spdx_id, is_copyleft | **yes**: license_id is not a key of R3 |
| R4 | vuln_id → osv_id → cvss_score | no: osv_id is a candidate key of R4 (G11), so this is not transitive through a non-key |
| R5, R6 | none | no |

Decompose the two violations:

| Split | Into |
|---|---|
| R1 | **SERVICE**(<ins>service_id</ins>, service_name, team_id) and **TEAM**(<ins>team_id</ins>, team_name) |
| R3 | **PACKAGE_VERSION**(<ins>package_id</ins>, <ins>version_string</ins>, license_id) and **LICENSE**(<ins>license_id</ins>, spdx_id, is_copyleft) |

## A.8 Boyce–Codd normal form (BCNF)

**Rule:** for every non-trivial FD `X → A`, X is a superkey. (Stricter than 3NF: 3NF still allows
A to be prime.)

| Relation | FDs inside it | Every left side a superkey? |
|---|---|---|
| TEAM | team_id → team_name | ✓ |
| SERVICE | service_id → service_name, team_id | ✓ |
| PACKAGE (R2) | package_id → package_name | ✓ |
| PACKAGE_ALIAS | none non-trivial | ✓ |
| PACKAGE_VERSION | package_id, version_string → license_id | ✓ |
| LICENSE | license_id → spdx_id, is_copyleft; spdx_id → license_id | ✓ both license_id and spdx_id are keys |
| VULNERABILITY (R4) | vuln_id → osv_id, cvss_score; osv_id → vuln_id | ✓ both are keys |
| AFFECTS (R5) | package_id, version_string, vuln_id → fixed_in | ✓ |
| RESOLVES_TO (R6) | none non-trivial | ✓ |

**All relations are in BCNF.** They are exactly the Phase 2 relations TEAM, SERVICE, PACKAGE,
PACKAGE_ALIAS, PACKAGE_VERSION, LICENSE, VULNERABILITY, AFFECTS and RESOLVES_TO (at service level),
so normalizing the flat report by hand arrives at the same design the ER model produced.

## A.9 Properties of the decomposition

### Lossless join

A binary decomposition of R into R1 and R2 is lossless if the shared attributes form a key of at
least one side: `(R1 ∩ R2) → R1` or `(R1 ∩ R2) → R2`.

| Split | R1 ∩ R2 | Key of | Lossless |
|---|---|---|---|
| 1NF table → R1 + rest | service_id | R1 | ✓ |
| rest → R2 + rest | package_id | R2 | ✓ |
| rest → R3 + rest | package_id, version_string | R3 | ✓ |
| rest → R4 + rest | vuln_id | R4 | ✓ |
| rest → R5 + rest | package_id, version_string, vuln_id | R5 | ✓ |
| R1 → SERVICE + TEAM | team_id | TEAM | ✓ |
| R3 → PACKAGE_VERSION + LICENSE | license_id | LICENSE | ✓ |

Each step is lossless, so the whole decomposition is lossless: joining the final tables gives back
exactly the original report, with no spurious rows.

Two steps are lossless for a reason other than this FD test:
- **The 1NF step** (moving `aliases` to PACKAGE_ALIAS) loses nothing, because every alias is stored
  next to its `package_id` and can be joined back.
- **The last 2NF step**, which keeps (service_id, package_id, version_string) as R6 and drops
  `vuln_id`, is lossless because of the multivalued dependency
  `package_id, version_string ↠ vuln_id`: the vulnerabilities of a version are independent of which
  service uses it, so R6 ⋈ R5 rebuilds every original row. Multivalued dependencies are the 4NF topic,
  one step past this syllabus.

### Dependency preservation

Every FD of the minimal cover lies entirely inside one final relation, so each can be checked
without a join:

| FD | Checked in |
|---|---|
| G1, G2 | SERVICE |
| G3 | TEAM |
| G4 | PACKAGE |
| G5 | PACKAGE_VERSION |
| G6, G7, G8 | LICENSE |
| G9, G10, G11 | VULNERABILITY |
| G12 | AFFECTS |

**The decomposition is lossless and dependency-preserving.** BCNF does not always allow both; here
it does.

---

# Part B — Normal-form check of all 23 relations

For each relation: its FDs (minimal), its candidate keys, and its highest normal form.
A relation is in BCNF when the left side of every non-trivial FD is a candidate key (or contains one).

| # | Relation | Functional dependencies | Candidate keys | NF |
|---|---|---|---|---|
| 1 | TEAM | team_id → team_name, contact_email, contact_slack; team_name → team_id | {team_id}, {team_name} | BCNF |
| 2 | SERVICE | service_id → service_name, criticality, team_id; service_name → service_id | {service_id}, {service_name} | BCNF |
| 3 | SCAN_RUN | scan_id → source_tool, started_at, ended_at | {scan_id} | BCNF |
| 4 | PACKAGE | package_id → package_name, ecosystem, cluster_id; package_name, ecosystem → package_id | {package_id}, {package_name, ecosystem} | BCNF |
| 5 | LICENSE | license_id → spdx_id, is_copyleft; spdx_id → license_id | {license_id}, {spdx_id} | BCNF |
| 6 | MAINTAINER | maintainer_id → handle, last_active_at; handle → maintainer_id | {maintainer_id}, {handle} | BCNF |
| 7 | SEMANTIC_CLUSTER | cluster_id → cluster_label | {cluster_id} | BCNF |
| 8 | VULNERABILITY | vuln_id → osv_id, severity, cvss_score; osv_id → vuln_id | {vuln_id}, {osv_id} | BCNF |
| 9 | REMEDIATION_PLAN | plan_id → status, created_at, team_id | {plan_id} | BCNF |
| 10 | DEPLOYMENT | service_id, deploy_seq → environment, deployed_at, is_active | {service_id, deploy_seq} | BCNF |
| 11 | PACKAGE_VERSION | package_id, version_string → published_at, sha256, license_id | {package_id, version_string} | BCNF |
| 12 | REMEDIATION_ACTION | plan_id, action_seq → action_type, target_version | {plan_id, action_seq} | BCNF |
| 13 | PR_REVIEW | service_id, pr_number → author, opened_at, filter_stage_exit | {service_id, pr_number} | BCNF |
| 14 | PR_RISK_VERDICT | service_id, pr_number, dep_name → verdict, reason | {service_id, pr_number, dep_name} | BCNF |
| 15 | RESOLVES_TO | service_id, deploy_seq, package_id, version_string → depth | the four key attributes | BCNF |
| 16 | DEPENDS_ON | dependent_package_id, dependent_version, dependency_package_id, dependency_version → scope, is_direct | the four key attributes | BCNF |
| 17 | MAINTAINED_BY | package_id, maintainer_id → role | {package_id, maintainer_id} | BCNF |
| 18 | AFFECTS | package_id, version_string, vuln_id → fixed_in | the three key attributes | BCNF |
| 19 | RESOLVES | none non-trivial (all-key) | {plan_id, action_seq, vuln_id} | BCNF |
| 20 | WAIVED_FOR | vuln_id, service_id → expires_on | {vuln_id, service_id} | BCNF |
| 21 | PACKAGE_ALIAS | none non-trivial (all-key) | {package_id, alias} | BCNF |
| 22 | MAINTAINER_EMAIL | none non-trivial (all-key) | {maintainer_id, email} | BCNF |
| 23 | OBSERVES | service_id, deploy_seq, scan_id, package_id, version_string → exec_count | the five key attributes | BCNF |

**All 23 relations are in BCNF**, and therefore also in 3NF, 2NF and 1NF.

Why each lower form holds:
- **1NF:** every attribute is atomic. The two multivalued attributes (alias, email) were already moved
  to their own relations in Phase 2, Step 6.
- **2NF:** the relations with a single-attribute key (1–9) cannot have partial dependencies. For the
  composite-key relations (10–23), the table above lists every non-key attribute and it depends on the
  whole key; the section below shows the partial dependencies that were checked and found not to hold.
- **3NF and BCNF:** in every relation, every determinant is a candidate key. The extra determinants
  (`team_name`, `service_name`, `spdx_id`, `handle`, `osv_id`, `package_name + ecosystem`) are all
  unique, so they are candidate keys, not sources of transitive dependency.

---

## Dependencies we checked and rejected

These are the FDs that *look* plausible and would have broken a normal form if they held. Each one
was checked against how the real data behaves.

| Suspected FD | Would violate | Why it does not hold |
|---|---|---|
| package_id, vuln_id → fixed_in (in AFFECTS) | 2NF (partial dependency) | A vulnerability can be fixed in different versions on different release lines. Log4Shell was fixed in 2.15.0 on the main line but also back-ported, for example to 2.12.2 for Java 7 and 2.3.1 for Java 6. So `fixed_in` depends on the affected version too: the whole key is needed. |
| package_id → license_id (in PACKAGE_VERSION) | 2NF | Projects change licence between releases, so the licence belongs to the version, not the package. This is also why LICENSED_UNDER is attached to PACKAGE_VERSION in the ER model. |
| cvss_score → severity (in VULNERABILITY) | 3NF (vuln_id → cvss_score → severity, transitive) | Severity is copied from the advisory database (for example GitHub's reviewed label), and the score may be missing or come from a different CVSS version. The label does not have to match the score band, so severity is stored as its own fact. If the project ever *computed* severity from the score, severity would become a derived attribute and be dropped from the table. |
| service_id, deploy_seq, package_id → depth (in RESOLVES_TO) | 2NF | One deployment can resolve two versions of the same package (common in npm), each at its own depth, so the version is needed. |
| service_id, pr_number → verdict (in PR_RISK_VERDICT) | 2NF | One pull request can add several dependencies, each with its own verdict. This is the reason PR_RISK_VERDICT is a separate weak entity. |
| dependent_package_id, dependent_version → scope (in DEPENDS_ON) | 2NF | A version declares different scopes for different dependencies (runtime for one, dev for another). |

## Why derived attributes are not stored

Storing a derived attribute would add a dependency on *other rows*, which no normal form can protect:

| Derived attribute | If stored, it would go stale when |
|---|---|
| SEMANTIC_CLUSTER.member_count | any PACKAGE row changes its cluster_id |
| PACKAGE.abandonment_score | a maintainer joins, leaves or becomes active again |
| SERVICE.exposure_score | a vulnerability, waiver or deployment changes |
| PACKAGE_VERSION.age | every day |

So they are computed by views or queries at read time (see `../er-diagram/schema.sql`, section 8).

---

## Summary

- Starting from one flat scan report, 1NF → 2NF → 3NF → BCNF decomposition produces the same
  relations as the ER-to-relational mapping, which confirms the ER design.
- The decomposition is **lossless** and **dependency-preserving**.
- All **23 relations are in BCNF**.
- Six plausible-looking dependencies were checked against real-world behaviour and shown not to hold.
