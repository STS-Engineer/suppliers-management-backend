# Evaluation Notifications — Setup & Operations Guide

Supplier evaluations are scheduled automatically based on scope:
- **Strategic** → every 3 months (Quarterly)
- **Global** → every 6 months (Semi-Annual)
- **Local** → every year (Annual)

Notifications are sent daily at 08:00 to all active `vp_conversion` and `purchasing_director` users
for any relation that is **Overdue**, **Due Soon** (within 30 days), or **Never Evaluated**.

---

## Architecture

```
pg_cron (inside PostgreSQL, 08:00 daily)
  → INSERT pending row into eval_notification_jobs
  → pg_notify('eval_due', ...)
        ↓
FastAPI listener (asyncpg, persistent connection)
  → wakes up instantly
  → marks job as running
  → queries overdue/due-soon relations
  → creates in-app notifications for vp_conversion + purchasing_director
  → marks job as completed

On app startup → scans for any pending rows missed while app was down
Manual button  → same logic, callable anytime from /evaluations page
```

---

## Prerequisites

- Azure PostgreSQL Flexible Server (Flexible Server supports pg_cron natively)
- `cron.database_name` server parameter set to `Suppliers-management-db`
- Alembic migration `20260701_0068` applied

---

## One-Time Setup

### 1. Set server parameter (Azure Portal or CLI)

**Azure Portal:**
1. PostgreSQL Flexible Server → **Server parameters**
2. Search `azure.extensions` → add `PG_CRON` → Save
3. Search `cron.database_name` → set to `Suppliers-management-db` → Save
4. Restart the server

**Azure CLI:**
```bash
az postgres flexible-server parameter set \
  --resource-group <rg> --server-name <server> \
  --name azure.extensions --value "PG_CRON"

az postgres flexible-server parameter set \
  --resource-group <rg> --server-name <server> \
  --name cron.database_name --value "Suppliers-management-db"

az postgres flexible-server restart \
  --resource-group <rg> --server-name <server>
```

---

### 2. Apply the migration

Run from the backend directory:

```bash
alembic upgrade head
```

This creates the `eval_notification_jobs` table in `Suppliers-management-db`.

---

### 3. Create the pg_cron extension

Connect to **`Action_Plan`** database in pgAdmin, then run:

```sql
CREATE EXTENSION IF NOT EXISTS pg_cron;
```

Verify:
```sql
SELECT * FROM pg_extension WHERE extname = 'pg_cron';
```

---

### 4. Register the cron jobs

Still connected to **`Action_Plan`**, run both:

```sql
-- Job 1: daily evaluation notifications at 08:00
SELECT cron.schedule_in_database(
  'eval-due-notifications',
  '0 8 * * *',
  $$
    INSERT INTO eval_notification_jobs (scheduled_for, status, source, created_at)
    VALUES (CURRENT_DATE, 'pending', 'pg_cron', NOW())
    ON CONFLICT (scheduled_for) DO NOTHING;

    SELECT pg_notify(
      'eval_due',
      json_build_object('date', CURRENT_DATE::text, 'source', 'pg_cron')::text
    );
  $$,
  'Suppliers-management-db'
);

-- Job 2: weekly cleanup every Sunday at 03:00
SELECT cron.schedule_in_database(
  'eval-jobs-cleanup',
  '0 3 * * 0',
  $$
    DELETE FROM eval_notification_jobs
    WHERE completed_at < NOW() - INTERVAL '90 days'
       OR (status = 'failed' AND completed_at < NOW() - INTERVAL '30 days');
  $$,
  'Suppliers-management-db'
);
```

Verify both jobs are registered:
```sql
SELECT jobid, jobname, schedule, database, active FROM cron.job;
```

---

## Testing

### Option A — Manual API call (fastest, no pg_cron needed)

```bash
curl -X POST https://<your-api>/api/v1/evaluations/trigger-notifications \
  -H "Authorization: Bearer <token>"
```

Or click **"Notify (N)"** on the `/evaluations` page as any non-viewer user.
Then log in as `vp_conversion` or `purchasing_director` and check the notification bell.

---

### Option B — Simulate pg_notify manually (tests the listener)

Connect to **`Suppliers-management-db`** in pgAdmin:

```sql
-- Reset if today's row already exists
DELETE FROM eval_notification_jobs WHERE scheduled_for = CURRENT_DATE;

-- Simulate what pg_cron does
INSERT INTO eval_notification_jobs (scheduled_for, status, source, created_at)
VALUES (CURRENT_DATE, 'pending', 'manual_test', NOW());

SELECT pg_notify(
  'eval_due',
  json_build_object('date', CURRENT_DATE::text, 'source', 'manual_test')::text
);
```

Watch FastAPI logs — you should see immediately:
```
eval_due notification received for 2026-07-01
Evaluation notifications sent: 2 recipient(s) for 2026-07-01
```

---

### Option C — Schedule a one-minute test job (tests actual pg_cron execution)

Connect to **`Action_Plan`**:

```sql
SELECT cron.schedule_in_database(
  'eval-test-run',
  '* * * * *',
  $$
    INSERT INTO eval_notification_jobs (scheduled_for, status, source, created_at)
    VALUES (CURRENT_DATE, 'pending', 'pg_cron_test', NOW())
    ON CONFLICT (scheduled_for) DO NOTHING;

    SELECT pg_notify(
      'eval_due',
      json_build_object('date', CURRENT_DATE::text, 'source', 'pg_cron_test')::text
    );
  $$,
  'Suppliers-management-db'
);
```

Wait ~1 minute, then **remove the test job immediately**:

```sql
SELECT cron.unschedule('eval-test-run');
```

---

## Monitoring & Diagnostics

### Check job history (Suppliers-management-db)

```sql
-- Last 10 job runs
SELECT id, scheduled_for, status, source,
       started_at, completed_at, notifications_sent, error
FROM eval_notification_jobs
ORDER BY created_at DESC
LIMIT 10;
```

### Check failed jobs

```sql
SELECT * FROM eval_notification_jobs
WHERE status = 'failed'
ORDER BY created_at DESC;
```

### Check if today's job ran

```sql
SELECT status, source, notifications_sent, completed_at
FROM eval_notification_jobs
WHERE scheduled_for = CURRENT_DATE;
```

### Check pg_cron execution log (Action_Plan)

pg_cron logs each execution in `cron.job_run_details`:

```sql
-- Last 20 executions across all jobs
SELECT jr.runid, j.jobname, jr.status, jr.start_time, jr.end_time, jr.return_message
FROM cron.job_run_details jr
JOIN cron.job j ON j.jobid = jr.jobid
ORDER BY jr.start_time DESC
LIMIT 20;
```

### Check rejected / errored pg_cron runs

```sql
SELECT jr.runid, j.jobname, jr.status, jr.return_message, jr.start_time
FROM cron.job_run_details jr
JOIN cron.job j ON j.jobid = jr.jobid
WHERE jr.status != 'succeeded'
ORDER BY jr.start_time DESC;
```

### List all registered cron jobs

```sql
-- run in Action_Plan
SELECT jobid, jobname, schedule, database, active, username
FROM cron.job
ORDER BY jobname;
```

---

## Operations

### Disable a job temporarily

```sql
-- run in Action_Plan
UPDATE cron.job SET active = false WHERE jobname = 'eval-due-notifications';
-- re-enable:
UPDATE cron.job SET active = true  WHERE jobname = 'eval-due-notifications';
```

### Remove a job permanently

```sql
SELECT cron.unschedule('eval-due-notifications');
SELECT cron.unschedule('eval-jobs-cleanup');
```

### Reset today's job to re-run (e.g. after a failure)

Connect to **`Suppliers-management-db`**:

```sql
-- Option 1: delete the row so pg_cron can re-insert it, or trigger manually
DELETE FROM eval_notification_jobs WHERE scheduled_for = CURRENT_DATE;

-- Option 2: reset a failed/stuck job back to pending so the startup scan picks it up
UPDATE eval_notification_jobs
SET status = 'pending', error = NULL, started_at = NULL
WHERE scheduled_for = CURRENT_DATE AND status IN ('failed', 'running');
```

### Manually process a pending job without pg_notify

If the listener missed the notification, call the API endpoint:

```bash
curl -X POST https://<your-api>/api/v1/evaluations/trigger-notifications \
  -H "Authorization: Bearer <token>"
```

---

## What users receive

| Role | What they get |
|---|---|
| `vp_conversion` | In-app notification with count of overdue / due soon / never evaluated |
| `purchasing_director` | Same |
| All other roles | Nothing automatic — they see the data on `/evaluations` page |

Notification content:
- **Title:** `Supplier evaluations require attention — 3 overdue, 2 due soon, 1 never evaluated`
- **Body:** Summary with link to open the Evaluation Scorecard
- **Action URL:** `/evaluations`
- **Frequency:** Once per day maximum (DB lock prevents duplicates)

---

## Job status reference

| Status | Meaning |
|---|---|
| `pending` | Created by pg_cron, not yet processed by the app |
| `running` | App picked it up, currently sending notifications |
| `completed` | Done — check `notifications_sent` for count |
| `failed` | Error occurred — check `error` column for details |

`pending` rows are automatically processed on app startup (missed-job recovery).
