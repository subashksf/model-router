# ADR-003: Store Routing Policy in YAML Files, Not the Database

**Status:** Accepted
**Date:** 2026-03-01
**Deciders:** Initial team

---

## Context

Each tenant needs a routing policy that maps complexity tiers to provider/model pairs, with optional feature-tag overrides. This config needs to be:
- Readable by engineers
- Versionable
- Changeable without code deploys
- Isolated per tenant

Two approaches were considered:

**Option A — YAML files per tenant, mounted into the gateway container.**
One file per tenant: `policies/<tenant_id>.yaml`. Falls back to `default_policy.yaml`.

**Option B — Database-backed policy store.**
A `routing_policies` table with a CRUD API. UI or CLI to edit.

---

## Decision

**Ship Option A (YAML files) for MVP.**

Rationale:
- Policy changes are infrequent (hours to days between changes, not seconds).
- YAML files are version-controlled in git, giving a full audit trail of policy changes.
- No additional DB table, migration, or admin API required for MVP.
- Engineers understand YAML. A DB-backed policy store requires a UI or CLI to be useful.
- The gateway process is stateless — policy files are read-only mounts, safe for horizontal scaling.
- `lru_cache` on policy loading means file I/O is paid only once per process lifetime.

The `policies/` directory is a first-class part of the repo and can be managed via standard GitOps workflows (PR review, CI validation of YAML schema).

---

## Consequences

**Positive:**
- Zero additional infrastructure for policy management.
- Policy changes are reviewable and auditable via git history.
- Simple to test: unit tests can write temp YAML files without mocking a DB.
- Customers with self-hosted deployments can manage policies via their existing config management tools (Helm, Ansible, etc.).

**Negative:**
- Policy changes require a config push + process restart (or at minimum a new container rollout). No live hot-reload in MVP.
- Not suitable for very high-frequency policy changes (e.g. feature flags that change per request).
- Adding a policy management UI later requires a DB-backed store, which means a migration path from files.

**Migration path (if needed):**
- Introduce a `PolicyStore` interface with `FilePolicyStore` and `DbPolicyStore` implementations.
- The DB-backed store can be bootstrapped by importing existing YAML files.
- The file-based store remains valid for self-hosted customers who prefer GitOps.
