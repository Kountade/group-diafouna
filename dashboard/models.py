from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone
from decimal import Decimal

User = get_user_model()

class DashboardPreferences(models.Model):
    """Préférences de tableau de bord par utilisateur"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='dashboard_preferences')
    widgets_order = models.JSONField(default=list, blank=True, help_text="Ordre des widgets")
    hidden_widgets = models.JSONField(default=list, blank=True, help_text="Widgets masqués")
    date_range = models.CharField(max_length=50, default='last_30_days', 
                                 help_text="Plage de dates par défaut")
    refresh_interval = models.IntegerField(default=30, help_text="Intervalle de rafraîchissement en secondes")
    theme = models.CharField(max_length=20, default='light', choices=[('light', 'Clair'), ('dark', 'Sombre')])
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Préférences de {self.user.email}"

    class Meta:
        verbose_name = "Préférence de tableau de bord"
        verbose_name_plural = "Préférences de tableau de bord"


class DashboardAlert(models.Model):
    """Alertes et notifications pour le tableau de bord"""
    ALERT_TYPES = (
        ('info', 'Information'),
        ('warning', 'Avertissement'),
        ('danger', 'Critique'),
        ('success', 'Succès'),
    )
    
    title = models.CharField(max_length=255)
    message = models.TextField()
    alert_type = models.CharField(max_length=20, choices=ALERT_TYPES, default='info')
    is_active = models.BooleanField(default=True)
    is_read = models.BooleanField(default=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, 
                            related_name='dashboard_alerts')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    
    def __str__(self):
        return f"{self.get_alert_type_display()}: {self.title}"

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Alerte"
        verbose_name_plural = "Alertes"


class DashboardWidget(models.Model):
    """Widgets configurables pour le tableau de bord"""
    WIDGET_TYPES = (
        ('stats', 'Statistiques'),
        ('chart', 'Graphique'),
        ('table', 'Tableau'),
        ('map', 'Carte'),
        ('activity', 'Activité récente'),
        ('alert', 'Alertes'),
        ('custom', 'Personnalisé'),
    )
    
    name = models.CharField(max_length=100)
    widget_type = models.CharField(max_length=20, choices=WIDGET_TYPES)
    config = models.JSONField(default=dict, help_text="Configuration du widget")
    is_system = models.BooleanField(default=False, help_text="Widget système non supprimable")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Widget"
        verbose_name_plural = "Widgets"


class DashboardMetric(models.Model):
    """Métriques calculées pour le tableau de bord"""
    METRIC_TYPES = (
        ('total_balance', 'Solde total'),
        ('total_transactions', 'Total transactions'),
        ('active_agents', 'Agents actifs'),
        ('total_partners', 'Total partenaires'),
        ('revenue', 'Revenus'),
        ('withdrawal_volume', 'Volume retraits'),
        ('deposit_volume', 'Volume dépôts'),
        ('custom', 'Personnalisé'),
    )
    
    name = models.CharField(max_length=100)
    metric_type = models.CharField(max_length=50, choices=METRIC_TYPES)
    value = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal('0.00'))
    previous_value = models.DecimalField(max_digits=20, decimal_places=2, default=Decimal('0.00'))
    change_percentage = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0.00'))
    calculated_at = models.DateTimeField(auto_now=True)
    date = models.DateField(default=timezone.now)
    
    def __str__(self):
        return f"{self.get_metric_type_display()}: {self.value}"

    class Meta:
        ordering = ['-date']
        verbose_name = "Métrique"
        verbose_name_plural = "Métriques"
        unique_together = ['metric_type', 'date']