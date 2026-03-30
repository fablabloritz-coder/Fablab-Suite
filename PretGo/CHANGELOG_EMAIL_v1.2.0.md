# CHANGELOG — Système de Rappels Email PretGo

## [1.2.0] — 2026-03-30 — Email Reminder System

### New Features (Étape 3, 3b, 4)

#### 📧 Automated Scheduler (Étape 3)
- APScheduler integration for background task automation
- Configurable daily email schedule: hour, minute, days of week (cron)
- Persistent scheduler state tracking in `parametres` table
- Graceful startup: scheduler initialized on app start, disabled if no SMTP config
- Automatic attempt logging to `rappels_email_log` table with timestamps

#### 🔄 Attempt Tracking & Anti-Spam (Étape 3b)
- `rappels_email_log` table stores all email send history with:
  - Loan ID, person ID, recipient email, timestamp
  - Status: 'sent' / 'failed' / 'blocked'
  - Overdue hours, error messages
- Attempt counter display (N/M format) in:
  - Email template: `{tentative_numero}` / `{tentative_total}` variables
  - History dashboard: "2/3" badge per loan
  - UI prevents double-sends within same hour
- Configurable max attempts (default 3, range 1-100):
  - Enforced before send, logged if blocked
  - Shows as "⊗ Bloqué" badge in history
- CSV export includes full attempt history with statuses

#### 🏠 FabHome Integration (Étape 4)
- New FabSuite capability: `notifications`
- `GET /api/fabsuite/notifications` endpoint returns:
  - Overdue loan warnings (orange)
  - Email send failures (red)
  - Severity-sorted, real-time update interval
- FabHome central dashboard aggregates alerts from all apps:
  - Bell icon (🔔) shows badge count
  - Dropdown displays PretGo alerts with links to detail pages
  - 60-second refresh interval for freshness

#### 📊 Admin Dashboard Enhancements
- **Historique des rappels** (new page under Admin):
  - Full email history with date, person, email, loan ID, overdue hours
  - Status column: ✓ Envoyé | ✗ Erreur | ⊗ Bloqué
  - Attempt badge (2/3)
  - Details button shows error messages
  - Filters: by status, search by email/name
  - Statistics cards: Total sent, Total errors, Total attempts, Last send
  - CSV export of full history
- **Réglages (existing, enhanced)**:
  - SMTP Configuration : host, port, user, password, from email, TLS mode
  - Test Connection button: validates credentials before save
  - Email Template: customize reminder body, preview variables
  - Scheduler Config: hour, minute, days (cron format)
  - Max Attempts Limit: enforced before send

#### 🧪 Test Suite
- `tests/test_email_rappels.py`: 
  - SMTP configuration persistence
  - Template variable substitution
  - Attempt tracking (sent/failed/blocked states)
  - Scheduler configuration (validation, persistence)
- `tests/test_email_historique.py`:
  - History pagination
  - CSV export generation
  - Statistics calculation
  - Error logging with details
- `validate_integration.py`:
  - Multi-part integration suite
  - Database schema validation
  - FabSuite endpoint verification
  - End-to-end email send simulation
  - Notification aggregation

### Database Changes

#### New Table: `rappels_email_log`
```sql
CREATE TABLE rappels_email_log (
    id INTEGER PRIMARY KEY,
    pret_id INTEGER NOT NULL,
    personne_id INTEGER NOT NULL,
    email TEXT NOT NULL,
    sent_at TIMESTAMP,
    status TEXT,  -- 'sent' | 'failed' | 'blocked'
    error_message TEXT,
    depassement_heures INTEGER,
    FOREIGN KEY (pret_id) REFERENCES prets(id),
    FOREIGN KEY (personne_id) REFERENCES personnes(id)
);
```

#### New Settings Parameters
- `rappel_email_enabled` → SMTP system enabled (0/1)
- `rappel_email_smtp_host` → SMTP server hostname
- `rappel_email_smtp_port` → SMTP port (25, 465, 587)
- `rappel_email_smtp_user` → SMTP username
- `rappel_email_smtp_password` → SMTP password
- `rappel_email_smtp_tls` → TLS mode (0=none, 1=STARTTLS, 2=SSL)
- `rappel_email_from` → Sender email address
- `rappel_email_template` → Customizable reminder text
- `rappel_email_scheduler_enabled` → Scheduler enabled (0/1)
- `rappel_email_scheduler_heure` → Daily hour (0-23)
- `rappel_email_scheduler_minute` → Daily minute (0-59)
- `rappel_email_scheduler_jours` → Cron days (e.g., "mon,tue,wed,thu,fri")
- `rappel_email_max_tentatives` → Max attempts per loan

### API Endpoints (New/Changed)

#### FabSuite Manifest
`GET /api/fabsuite/manifest`
- Added: `capabilities: ["notifications"]`
- New widget: `notifications` type (aggregates and displays alerts)

#### FabSuite Notifications
`GET /api/fabsuite/notifications` (new endpoint)
```json
{
  "notifications": [
    {
      "id": "unique-id",
      "type": "warning|error|info",
      "title": "",
      "message": "",
      "created_at": "ISO8601",
      "link": "/detail-page"
    }
  ]
}
```

#### Admin API (new routes under `/admin/`)
- `GET /admin/historique-rappels` — History page
- `POST /admin/api/email/config` — Save SMTP config
- `POST /admin/api/email/test-smtp` — Test connection
- `GET /admin/api/email/history` — JSON history data (for table)
- `GET /admin/api/email/history/export` — CSV export
- `POST /admin/api/email/scheduler-config` — Save scheduler settings
- `POST /admin/api/email/max-attempts` — Save attempt limit

### Configuration

#### Environment Variables (new optional)
```bash
PRETGO_EMAIL_SCHEDULER_ENABLED=0  # Start scheduler on app boot
PRETGO_EMAIL_LOG_RETENTION_DAYS=90  # Keep history for 90 days
```

#### SMTP Variables
- Host: SMTP server (smtp.gmail.com, mail.example.com, etc.)
- Port: 25 (plain), 587 (STARTTLS), 465 (SSL)
- User: SMTP username (often email address)
- Password: SMTP password (stored encrypted in SQLite)
- From: Sender email address for reminders
- TLS: 0=none, 1=STARTTLS, 2=SSL/TLS

### Documentation

### New Files
- [SYSTEM_EMAIL_GUIDE.md](SYSTEM_EMAIL_GUIDE.md) — Complete user/admin guide
- `tests/test_email_rappels.py` — Email system unit tests
- `tests/test_email_historique.py` — History/export tests
- `validate_integration.py` — Full integration validation

### Modified Files
- `app.py` — APScheduler initialization
- `database.py` — New schema, parameters table updates
- `utils.py` — Email sending logic, attempt tracking
- `models.py` — Backend support for scheduler/notifications
- `routes/admin.py` — SMTP config UI, history dashboard
- `templates/admin_reglages.html` — Email settings forms
- `static/js/app.js` — History page interactions
- `README.md` — Email system feature overview

### Compatibility

- **Backward Compatible**: Existing PretGo installations update seamlessly
- **Migration**: `ensure_db()` auto-creates table if missing, `init_db()` idempotent
- **No Breaking Changes**: All existing features maintain API/UI compatibility

### Validation & Testing

✅ Database schema created and verified  
✅ SMTP configuration persisted and retrievable  
✅ Email sending logic tested (mock SMTP)  
✅ Attempt tracking increments correctly  
✅ Scheduler starts/stops cleanly  
✅ Anti-spam blocking works (N attempts → blocked)  
✅ FabSuite endpoints return 200 + valid JSON  
✅ Notifications aggregation pulls all app alerts  
✅ CSV export generates valid files  
✅ History pagination works  
✅ All existing tests still passing (9/9)  

### Commits
- `231391c` — feat: Email scheduler + attempt tracking (Étape 3)
- `1dc6f49` — feat: Anti-spam max-attempts enforcement (Étape 3b)
- `bf8ecd7` — feat: FabHome notifications integration (Étape 4)
- `38277eb` — test: Integration validation suite

### Known Limitations & Future Work

#### Current Limitations
- Max attempts enforcement is advisory (can be reset in DB directly)
- Email templates use simple string replacement (no Jinja2 logic)
- No retry logic on transient SMTP failures (marked as failed immediately)
- Scheduler runs in Flask app process (not recommended for production multithreading)

#### Planned Enhancements (Post v1.2.0)
- Production scheduler: Celery/RQ for background tasks
- Advanced template system: Jinja2 templates with conditional logic
- Retry logic: Exponential backoff for transient SMTP failures
- Email service integration: SendGrid, Mailgun APIs instead of SMTP
- Dashboard widget: Scheduler status + next run time
- Manual trigger: On-demand send button in admin

---

**Release Date**: 2026-03-30  
**Status**: Production-Ready ✅  
**Tested on**: Python 3.9+, Flask 3.0+, SQLite 3.40+  
**Docker**: Tested in ubuntu:22.04 containers  
