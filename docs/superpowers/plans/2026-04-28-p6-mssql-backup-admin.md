# P6 MSSQL Backup Admin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Django app that manages scheduled backup and guarded restore for each visible P6 SQL Server database target.

**Architecture:** The project uses SQLite for local metadata, Celery/django-celery-beat for schedules, pyodbc for SQL Server operations, and Microsoft Graph for SharePoint storage. Each `BackupTarget` owns its connection, paths, schedule, retention, and notification settings.

**Tech Stack:** Django 5.0, Celery, django-celery-beat, Redis, pyodbc, cryptography Fernet, requests, Docker Compose.

---

### Task 1: Project Scaffold

**Files:**
- Create: `manage.py`
- Create: `p6_backup_admin/settings.py`
- Create: `p6_backup_admin/urls.py`
- Create: `p6_backup_admin/celery.py`
- Create: `requirements.txt`
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.env.example`

- [x] Create a minimal Django project with local SQLite metadata and Celery configuration.
- [x] Add Docker services for web, worker, beat, and Redis.
- [x] Mount the P6 backup folders into the Django container.

### Task 2: Models And Defaults

**Files:**
- Create: `backups/models.py`
- Create: `backups/admin.py`
- Create: `backups/management/commands/bootstrap_p6_targets.py`
- Create: `backups/migrations/0001_initial.py`

- [x] Add `BackupTarget` and `BackupRun`.
- [x] Bootstrap separate targets for `AxialP6`, `Axial Training`, and `P62212`.
- [x] Keep SQL passwords in environment variables instead of model fields.

### Task 3: MSSQL Services

**Files:**
- Create: `backups/services/mssql.py`
- Create: `backups/services/security.py`
- Create: `backups/services/manifest.py`
- Create: `backups/services/orchestration.py`
- Create: `backups/services/restore.py`

- [x] Generate SQL Server-native backup and restore statements.
- [x] Verify backups with `RESTORE VERIFYONLY`.
- [x] Encrypt and decrypt large backups with chunked Fernet.
- [x] Build restore `WITH MOVE` clauses from `RESTORE FILELISTONLY`.

### Task 4: Graph, Retention, Email, Scheduling

**Files:**
- Create: `graph/services/tokens.py`
- Create: `graph/services/email.py`
- Create: `backups/services/sharepoint.py`
- Create: `backups/services/retention.py`
- Create: `backups/services/emailing.py`
- Create: `backups/services/scheduling.py`
- Create: `backups/tasks.py`

- [x] Implement Graph client credential token retrieval.
- [x] Upload/download/list/delete SharePoint backup files.
- [x] Create one periodic task per enabled target.
- [x] Send status emails through Graph when configured, otherwise Django email.

### Task 5: Templates And Views

**Files:**
- Create: `backups/views.py`
- Create: `backups/urls.py`
- Create: `templates/base.html`
- Create: `templates/backups/dashboard.html`
- Create: `templates/backups/target_detail.html`
- Create: `templates/backups/remote_backups.html`
- Create: `templates/registration/login.html`

- [x] Add staff-only pages for target settings, manual backup, history, and restore.
- [x] Require typed database-name confirmation before restore.

### Task 6: Tests And Verification

**Files:**
- Create: `backups/tests/test_mssql.py`
- Create: `backups/tests/test_security.py`
- Create: `backups/tests/test_sharepoint.py`
- Create: `backups/tests/test_retention.py`
- Create: `backups/tests/test_views.py`

- [x] Cover pure service behavior without live SQL Server or Microsoft Graph.
- [x] Run Django tests.
- [x] Run `manage.py check`.
