# Snowflake data source — operator manual

How to connect Snowflake to OptScale, choose billing source, set cost model, and verify imports.

## Architecture (two data sources)

Snowflake billing in OptScale uses **two** `cloud_type=snowflake` sources when you need both org-wide costs and account-level AI:

| Data source | `billing_source` | What it imports |
|-------------|------------------|-----------------|
| Organization admin account | `organization_usage` | Warehouse compute, storage, stage, Snowpipe, auto-clustering, remaining serverless (`METERING_DAILY` **including AI aggregates** such as `SNOWFLAKE_COCO_*` / `AI_FUNCTIONS`), data transfer, marketplace, currency adjustments, listing auto-fulfillment |
| Member / prod account (optional) | `account_usage` | **Detailed** Cortex / Intelligence (model, tokens, …), reader warehouses, listing consumption analytics |

**Recommended billing view:** use the **organization** (`organization_usage`) data source for org totals, including AI. Member `account_usage` sources are for Cortex **detail** only — do **not** sum ADMIN + PROD AI in Cost Explorer (same credits appear twice).

Shared non-AI usage (warehouse, storage, …) stays only on `organization_usage`. Member sources must not re-import those.

## Prerequisites (Snowflake)

1. **Key-pair auth** for a service user (PKCS#8 PEM private key in OptScale; public key on the Snowflake user).
2. **Warehouse** available to that user (queries run there).
3. Roles / grants:
   - `organization_usage`: read on `SNOWFLAKE.ORGANIZATION_USAGE.*` (typically org account + `ORGADMIN` or a custom role with those grants).
   - `account_usage`: read on `SNOWFLAKE.ACCOUNT_USAGE.*`, Cortex views, and (for listing analytics) `SNOWFLAKE.DATA_SHARING_USAGE.LISTING_CONSUMPTION_DAILY`.

Example (adjust role/user names):

```sql
ALTER USER SVC_OPTSCALE SET RSA_PUBLIC_KEY='MIIBIjAN...';
GRANT USAGE ON WAREHOUSE INFRASTRUCTURE_TEST_WH TO ROLE ACCOUNTADMIN;
-- Plus ORGANIZATION_USAGE / ACCOUNT_USAGE / DATA_SHARING_USAGE grants as needed
```

## Connect in UI

1. **Data sources → Connect** → Snowflake.
2. Fill: account identifier, user, private key (PEM), role, warehouse.
3. Select **Billing source**:
   - Organization usage — org billing.
   - Account usage — AI + account-only views.
4. After create, open **Cost model** and set:
   - `credit_price` (contractual $ / credit),
   - `storage_price_per_tb_month` (default often ~23).

Import period is the same scheduler cadence as other sources (typically 6h). First import looks back ~90 days; later runs rewind ~2 days for late-arriving rows.

### Reimport behavior

A billing reimport (or any run after moving `last_import_at` backward) does **not** wipe the resource catalog for the account.

- **Expenses** (Mongo raw + ClickHouse) for the chosen window are cleared and reloaded.
- **Resources** are created or updated in place (`cloud_resource_id` is stable for Snowflake collectors).
- Resources that simply had **no usage in that window** stay as they are (no “orphan” soft-delete).
- The only automatic resource soft-delete is for **known legacy id shapes** left over from earlier OptScale id/schema renames (old `…/stages`, old Cortex id patterns). Snowflake itself does not re-key the same object under a new locator-based id.

## Account locator and account name

Snowflake **account locator** (for example `HW44440`, `CP81654`) is stored on each resource as a first-class field `account_locator`. It is the stable account id from usage rows (not the OptScale data source name).

**Account name** (for example `PUBLICIS_PROD`) is loaded with it:

- From per-row `ACCOUNT_NAME` on `ORGANIZATION_USAGE` views, and
- From `SNOWFLAKE.ORGANIZATION_USAGE.ACCOUNTS` (locator → name map) so every member account on an org source gets a name, not only the admin session account.
- On `account_usage`, from `CURRENT_ACCOUNT_NAME()` when the view has no name column.

Where they appear in the UI:

- **Resources** table → column **Account locator** (name on top, locator below when both exist)
- **Resources** → filters → **Account locator**
- **Resources / Expenses (META)** → **Categorize by** → **Account locator**
- Available-filters API / breakdowns that expose `account_locator`

On an `organization_usage` source this is how you split org-wide spend by member Snowflake account. On `account_usage` it is usually a single locator for that account.

## Verify import

### Advanced → per-collector stats (recommended)

**Data sources → open Snowflake source → Advanced.**

Each Snowflake import publishes per-collector stats on that tab: `service_type`, `records`, `credits` / bytes, `status` (`ok` / `skipped` / `failed`), `message`.

Look for:

- Org source: `COMPUTE`, `STORAGE`, `AUTOMATIC_CLUSTERING`, `LISTING_AUTO_FULFILLMENT`, …
- Account source: `AI_SERVICES`, `READER_ACCOUNT`, `LISTING_CONSUMPTION`, …

Advanced also shows **Billing data period** (min/max expense dates for that source). The same period is shown as a column on the **Data sources** table for Snowflake.

### Cost Explorer (expenses)

**Expenses / META → Categorize by `service_type`** (or **Account locator**).

You should see money for `COMPUTE`, `STORAGE`, `AI_SERVICES`, `LISTING_AUTO_FULFILLMENT`, etc.

**Note:** `LISTING_CONSUMPTION` is **provider analytics** (jobs / unique users). It has **cost = $0** and will not move the expense chart. Use Resources or Advanced collector stats to confirm it.

### Resources

**Resources** → filter by data source and optionally **Account locator** / resource type:

- `LISTING_CONSUMPTION` — listing + consumer activity (`jobs`, `unique_users_1d` in meta).
- `AI_SERVICES` — Cortex / Intelligence spend resources.

### API (cluster secret)

```bash
# Schedule import
curl -s -X POST -H "Secret: <cluster_secret>" -H "Content-Type: application/json" \
  -d '{"cloud_account_id":"<id>"}' \
  https://<host>/restapi/v2/schedule_imports

# Import details (same stats as Advanced)
curl -s -H "Secret: <cluster_secret>" \
  https://<host>/restapi/v2/report_imports/<import_id>
```

Optional: set `last_import_at` (unix seconds) via `PATCH /restapi/v2/cloud_accounts/<id>` before scheduling to widen the window.

## Cost correctness checklist

1. Org DS imports warehouse + storage + AI aggregates; use it for billing totals.
2. Member `account_usage` is optional detail for Cortex — filter Cost Explorer to ADMIN alone when checking money.
3. After changing credit/storage prices, wait for recalculate (or trigger reimport) and re-check Cost Explorer totals.
4. Reconciliation warnings on Advanced (detail vs `METERING_DAILY`) are non-blocking; investigate if consistently > ~5%.

## Troubleshooting

| Symptom | What to check |
|---------|----------------|
| Connect fails | PEM format, public key on user, warehouse, role |
| Collector `skipped` / warning | View missing for edition/region; grant on that view |
| Double COMPUTE/STORAGE | Two sources both on overlapping collectors (should not happen after ACCOUNT/ORG split + purge migration) |
| AI appears on both ADMIN and PROD | Expected if member DS is connected — use ADMIN for billing totals |
| Empty `LISTING_CONSUMPTION` | No marketplace listing traffic in range, or no grant on `DATA_SHARING_USAGE` |
| Import stuck / 409 in progress | Wait for active import; check diworker logs; etcd lock `/_locks/diworker_migrations` only if migrations hang |

See also: `documentation/snowflake-integration-plan.md`, `documentation/k8s_deploy_cloud_engineer.md`.
