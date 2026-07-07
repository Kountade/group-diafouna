# finance/apps.py
from django.apps import AppConfig

class FinanceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'finance'

    def ready(self):
        # Importez les signaux uniquement si l'application est prête
        import finance.signals