# PretGo Email Reminder System — Complete Guide

## Overview

PretGo's email reminder system automatically sends notifications for overdue equipment loans using **automatic scheduler**, **anti-spam protections**, and **FabHome integration**.

## Features

### 1. Automated Scheduler (Étape 3)
- **What it does**: Sends email reminders daily at configured time
- **Configuration**: Admin → Réglages → "Scheduler automatique"
  - **Enable**: Toggle to activate/disable automated sends
  - **Heure** (Hour): 0-23 (military time)
  - **Minute**: 0-59 (step 15 recommended)
  - **Jours** (Days): Cron format (e.g., `mon,tue,wed,thu,fri` or `*` for all days)

### 2. Attempt Tracking (Étape 3)
- **What it does**: Tracks how many times each loan has been sent an email reminder
- **Display**: 
  - In emails: `Tentative: 2/3` shows current attempt vs total
  - In history: "Tentative" column displays N/M badge
- **Database**: Table `rappels_email_log` stores every attempt with timestamp and status

### 3. Anti-Spam Limits (Étape 3b)
- **What it does**: Prevents email overload by limiting reminders per loan
- **Configuration**: Admin → Réglages → "Limite de tentatives"
  - **Max tentatives**: Default 3 (min 1, max 100)
- **Behavior**: 
  - When limit reached, email is NOT sent
  - Log entry created with status "blocked"
  - Shown as "⊗ Bloqué" badge in history

### 4. FabHome Notifications (Étape 4)
- **What it does**: Displays alerts in FabHome central dashboard
- **Features**:
  - Overdue loans listed as warnings
  - Email send failures listed as errors
  - Real-time updates (refreshed every 60 seconds)
  - Severity-sorted (errors first, then warnings, then info)
- **Access**: FabHome top-right bell icon → dropdown shows PretGo alerts

## Configuration

### Basic Setup

1. **Enable Email System**
   - Admin → Réglages → "Configuration SMTP"
   - Check "Activer rappels email"
   - Configure SMTP credentials (host, port, user, password)
   - Set sender email (`rappel_email_from`)

2. **Test Connection**
   - Click "Tester connexion SMTP" button
   - Should see "Connexion SMTP réussie ✓"

3. **Configure Template**
   - Admin → Réglages → "Template de rappel"
   - Edit body text
   - Available variables: `{nom}`, `{prenom}`, `{objets}`, `{date_emprunt}`, `{depassement}`, `{tentative_numero}`, `{tentative_total}`
   - Example: `"Bonjour {prenom}, ceci est rappel #{tentative_numero}/{tentative_total}..."`

### Scheduler Setup

1. **Enable Automatic Sends**
   - Admin → Réglages → "Scheduler automatique"
   - Check "Activer l'envoi automatique"

2. **Set Schedule**
   - **Heure**: 9 (sends at 09:00)
   - **Minute**: 0
   - **Jours**: `mon,tue,wed,thu,fri` (weekdays only)
   - Click "Enregistrer scheduler"

3. **Verify**
   - Check application logs for "Scheduler d'email démarré..."
   - Task runs automatically at configured time

### Anti-Spam Configuration

1. **Set Maximum Attempts**
   - Admin → Réglages → "Limite de tentatives"
   - Set to desired limit (default 3)
   - Click "Enregistrer limite"

2. **Behavior**
   - After 3rd attempt, emails stop being sent
   - Still visible in history as "⊗ Bloqué"
   - Admin must manually handle loan (return or write-off)

## Monitoring

### History Dashboard

**Access**: Admin → Historique des rappels

**Columns**:
- Date: When email was sent
- Personne: Borrower name
- Email: Recipient email address
- Prêt ID: Loan ID
- Dépassement: Days overdue
- Tentative: Current attempt (e.g., 2/3)
- Statut: "✓ Envoyé", "✗ Erreur", "⊗ Bloqué"
- Détails: Error message if applicable

**Filters**:
- Statut: All / Sent / Failed
- Search: By email, name, or first name

**Export**: CSV with full history data

### Statistics

Four cards at top of history page:
- **Total envoyés**: Successfully sent reminders
- **Erreurs**: Failed sends (connection, auth issues)
- **Total tentatives**: All attempts (success + failure + blocked)
- **Dernier envoi**: When last queue ran

### FabHome Integration

**View Notifications**:
1. Login to FabHome
2. Click bell icon (🔔) in top-right
3. See:
   - Overdue loans (orange warning badge)
   - Email failures (red error badge)
4. Click alert to navigate to detail page

## API Endpoints (FabSuite)

### Manifest
```
GET /api/fabsuite/manifest
```
Response includes:
- `capabilities`: ["loans", "inventory", "notifications"]
- `widgets`: 3 widgets (active-loans, overdue-loans, equipment-status)
- `notifications.endpoint`: `/api/fabsuite/notifications`

### Notifications
```
GET /api/fabsuite/notifications
```
Response:
```json
{
  "notifications": [
    {
      "id": "overdue-loan-123",
      "type": "warning",
      "title": "Prêt en retard: Jean Dupont",
      "message": "Scie à métaux — retard de 5j 3h",
      "created_at": "2026-03-25T14:30:00",
      "link": "/pret/123"
    },
    {
      "id": "email-failed-456",
      "type": "error",
      "title": "Erreur d'envoi email vers test@example.com",
      "message": "2 tentative(s) échouée(s) pour prêt #456",
      "link": "/admin/historique-rappels"
    }
  ]
}
```

## Troubleshooting

### Scheduler Not Starting
**Problem**: "Scheduler d'email démarré" not in logs

**Solutions**:
1. Check Docker logs: `docker logs pretgo` (or `docker-compose logs`)
2. Verify `apscheduler` installed: `pip list | grep apscheduler`
3. Check if scheduler enabled: Admin → Réglages → "Activer l'envoi automatique"
4. Restart container: `docker-compose restart` or restart app process

### Emails Not Sending
**Problem**: "Statut: ✗ Erreur" in history

**Solutions**:
1. Click error detail button to see message
2. Test SMTP: Admin → "Tester connexion SMTP"
3. Check credentials in settings
4. Verify TLS/SSL mode (one or the other, not both)
5. Check firewall/port access to SMTP server

### Notifications Not Showing in FabHome
**Problem**: Bell icon shows 0, or no PretGo alerts

**Solutions**:
1. Verify PretGo registered in FabHome: Admin → Services FabLab Suite → PretGo row
2. Check PretGo health: `/api/fabsuite/health` returns true
3. Click "Tester PretGo" button in FabHome settings
4. Force refresh FabHome: F5 or `docker-compose restart fabhome`
5. Check FabHome logs for connection errors

## Database Schema

### New Tables/Columns

**`rappels_email_log`** (existing, used for tracking):
- `id`: Primary key
- `pret_id`: Loan ID (FK to prets)
- `personne_id`: Person ID (FK to personnes)
- `email`: Recipient email
- `sent_at`: Timestamp
- `status`: 'sent' | 'failed' | 'blocked'
- `error_message`: Error details if applicable
- `depassement_heures`: Overdue hours at time of send

**`parametres`** (new settings):
- `rappel_email_scheduler_enabled`: '0' or '1'
- `rappel_email_scheduler_heure`: '09'
- `rappel_email_scheduler_minute`: '00'
- `rappel_email_scheduler_jours`: 'mon,tue,wed,thu,fri'
- `rappel_email_max_tentatives`: '3'

## Development

### Running Tests
```bash
# All email tests
python test_email_rappels.py

# History and stats
python test_email_historique.py

# Full integration validation
python validate_integration.py
```

### Key Files
- `utils.py`: Email sending logic, attempt tracking
- `scheduler.py`: APScheduler wrapper
- `routes/__init__.py`: FabSuite manifest and widgets
- `database.py`: Schema and defaults
- `routes/admin.py`: Admin endpoints and UI handlers
- `templates/admin_reglages.html`: Configuration UI
- `templates/admin_historique_rappels.html`: History display

### Environment Variables
```bash
# .env file
FLASK_SECRET_KEY=<long-random-key>
TZ=Europe/Paris  # Timezone for scheduler
```

## Deployment

### Docker
```bash
cd PretGo
git pull origin main
docker-compose up -d --build
```

### Verification
```bash
# Check health
curl http://localhost:5000/api/fabsuite/health

# Get manifest
curl http://localhost:5000/api/fabsuite/manifest | jq

# List notifications
curl http://localhost:5000/api/fabsuite/notifications | jq
```

## Support & Future

### Known Limitations
- Maximum attempts enforcement is advisory only (can be reset in DB)
- Email templates use simple string replacement (no Jinja2)
- No email retry logic on temporary failure (marked as failed immediately)

### Planned Enhancements
- Dashboard widget showing scheduler status / next run time
- Manual trigger button for on-demand sends
- Email template builder UI (drag-drop variables)
- Configurable max attempts per user/department
- Integration with email service providers (SendGrid, Mailgun) instead of SMTP

---

**Last Updated**: 2026-03-30
**Version**: 1.0.0
**System Status**: Production-Ready ✅
