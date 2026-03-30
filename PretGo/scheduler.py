"""
Scheduler pour envoi automatique des rappels email PretGo.
Utilise APScheduler pour déclencher les envois selon configuration.
"""

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
import logging

_log = logging.getLogger(__name__)


class EmailReminderScheduler:
    """Gestionnaire du scheduler pour envois automatiques."""
    
    def __init__(self):
        self.scheduler = None
        self.job_id = 'email_rappels_auto'
    
    def start(self, app):
        """Démarre le scheduler avec la configuration depuis les paramètres."""
        if self.scheduler is not None and self.scheduler.running:
            return  # Déjà en cours d'exécution
        
        from database import get_db, get_setting
        from utils import envoyer_rappels_alertes_email
        
        # Créer le scheduler
        self.scheduler = BackgroundScheduler()
        
        # Charger la configuration
        conn = get_db()
        scheduler_enabled = get_setting('rappel_email_scheduler_enabled', '0', conn=conn) == '1'
        scheduler_hour = get_setting('rappel_email_scheduler_heure', '09', conn=conn).strip() or '09'
        scheduler_minute = get_setting('rappel_email_scheduler_minute', '00', conn=conn).strip() or '00'
        scheduler_jours = get_setting('rappel_email_scheduler_jours', 'mon,tue,wed,thu,fri', conn=conn).strip() or 'mon,tue,wed,thu,fri'
        conn.close()
        
        if scheduler_enabled:
            try:
                # Trigger CRON : chaque jour aux horaires spécifiés
                trigger = CronTrigger(
                    day_of_week=scheduler_jours,
                    hour=scheduler_hour,
                    minute=scheduler_minute,
                    timezone='Europe/Paris'
                )
                
                # Enregistrer la tâche
                def task_envoyer_rappels():
                    """Tâche de fond : envoie les rappels."""
                    try:
                        conn = get_db()
                        stats = envoyer_rappels_alertes_email(conn)
                        conn.commit()
                        conn.close()
                        
                        msg = f"Rappels email: {stats.get('envoyes', 0)} envoyés, {stats.get('echecs', 0)} erreurs"
                        _log.info(msg)
                    except Exception as e:
                        _log.error(f'Erreur lors de l\'envoi automatique des rappels: {str(e)[:200]}')
                
                self.scheduler.add_job(
                    task_envoyer_rappels,
                    trigger=trigger,
                    id=self.job_id,
                    name='Envoi automatique rappels email',
                    replace_existing=True
                )
                
                self.scheduler.start()
                _log.info(f'Scheduler d\'email démarré : {scheduler_jours} à {scheduler_hour}:{scheduler_minute}')
            except Exception as e:
                _log.error(f'Erreur lors du démarrage du scheduler: {str(e)[:200]}')
    
    def stop(self):
        """Arrête le scheduler."""
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown()
            _log.info('Scheduler d\'email arrêté')
    
    def restart(self, app):
        """Redémarre le scheduler (pour recharger la configuration)."""
        self.stop()
        self.start(app)


# Instance globale du scheduler (sera instanciée dans app.py)
email_scheduler = EmailReminderScheduler()
