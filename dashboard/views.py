from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db import transaction
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
import json

from .models import DashboardPreferences, DashboardAlert, DashboardWidget, DashboardMetric
from .serializers import (
    DashboardPreferencesSerializer, DashboardAlertSerializer,
    DashboardWidgetSerializer, DashboardMetricSerializer,
    GlobalStatsSerializer, TrendDataSerializer, TopPerformersSerializer,
    WithdrawalAnalyticsSerializer, BalanceSnapshotSerializer
)
from .services import DashboardService

User = get_user_model()


class DashboardViewSet(viewsets.ViewSet):
    """ViewSet principal pour le tableau de bord"""
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        if self.action in ['get_stats', 'get_trends', 'get_top_performers',
                           'get_withdrawal_analytics', 'get_balance_snapshot']:
            if self.request.user.role not in ['admin', 'agent']:
                self.permission_classes = [permissions.IsAuthenticated]
        return super().get_permissions()

    @action(detail=False, methods=['get'], url_path='stats')
    def get_stats(self, request):
        """Obtenir les statistiques globales"""
        date_range = request.query_params.get('date_range', 'last_30_days')

        try:
            stats = DashboardService.get_global_stats(date_range)
            return Response(stats)
        except Exception as e:
            return Response(
                {'error': f'Erreur lors de la récupération des statistiques: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'], url_path='trends')
    def get_trends(self, request):
        """Obtenir les tendances"""
        date_range = request.query_params.get('date_range', 'last_30_days')

        try:
            trends = DashboardService.get_trends(date_range)
            return Response(trends)
        except Exception as e:
            return Response(
                {'error': f'Erreur lors de la récupération des tendances: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'], url_path='top-performers')
    def get_top_performers(self, request):
        """Obtenir les meilleurs performeurs"""
        limit = request.query_params.get('limit', 10)

        try:
            top_performers = DashboardService.get_top_performers(int(limit))
            return Response(top_performers)
        except Exception as e:
            return Response(
                {'error': f'Erreur lors de la récupération des meilleurs performeurs: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'], url_path='agent-performance/(?P<agent_id>[0-9]+)')
    def get_agent_performance(self, request, agent_id=None):
        """Obtenir les performances d'un agent"""
        date_range = request.query_params.get('date_range', 'last_30_days')

        try:
            performance = DashboardService.get_agent_performance(
                int(agent_id), date_range)
            if 'error' in performance:
                return Response(performance, status=status.HTTP_404_NOT_FOUND)
            return Response(performance)
        except Exception as e:
            return Response(
                {'error': f'Erreur lors de la récupération des performances: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'], url_path='withdrawal-analytics')
    def get_withdrawal_analytics(self, request):
        """Obtenir les analyses des retraits"""
        date_range = request.query_params.get('date_range', 'last_30_days')

        try:
            analytics = DashboardService.get_withdrawal_analytics(date_range)
            return Response(analytics)
        except Exception as e:
            return Response(
                {'error': f'Erreur lors de l\'analyse des retraits: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'], url_path='balance-snapshot')
    def get_balance_snapshot(self, request):
        """Obtenir l'instantané des soldes"""
        try:
            snapshot = DashboardService.get_balance_snapshot()
            return Response(snapshot)
        except Exception as e:
            return Response(
                {'error': f'Erreur lors de la récupération des soldes: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'], url_path='alerts')
    def get_alerts(self, request):
        """Obtenir les alertes"""
        try:
            alerts = DashboardService.get_alerts(
                request.user.id if request.user.role != 'admin' else None)
            return Response(alerts)
        except Exception as e:
            return Response(
                {'error': f'Erreur lors de la récupération des alertes: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['post'], url_path='alerts/(?P<alert_id>[0-9]+)/read')
    def mark_alert_read(self, request, alert_id=None):
        """Marquer une alerte comme lue"""
        try:
            from .models import DashboardAlert
            alert = DashboardAlert.objects.get(id=alert_id)

            # Vérifier les permissions
            if alert.user and alert.user.id != request.user.id and request.user.role != 'admin':
                return Response(
                    {'error': 'Non autorisé'},
                    status=status.HTTP_403_FORBIDDEN
                )

            alert.is_read = True
            alert.save()

            return Response({'message': 'Alerte marquée comme lue'})
        except DashboardAlert.DoesNotExist:
            return Response(
                {'error': 'Alerte non trouvée'},
                status=status.HTTP_404_NOT_FOUND
            )

    @action(detail=False, methods=['get'], url_path='summary')
    def get_dashboard_summary(self, request):
        """Obtenir un résumé complet du tableau de bord"""
        date_range = request.query_params.get('date_range', 'last_30_days')

        try:
            with transaction.atomic():
                stats = DashboardService.get_global_stats(date_range)
                trends = DashboardService.get_trends(date_range)
                top_performers = DashboardService.get_top_performers(5)
                withdrawal_analytics = DashboardService.get_withdrawal_analytics(
                    date_range)
                balance_snapshot = DashboardService.get_balance_snapshot()
                alerts = DashboardService.get_alerts(
                    request.user.id if request.user.role != 'admin' else None
                )

            return Response({
                'stats': stats,
                'trends': trends,
                'top_performers': top_performers,
                'withdrawal_analytics': withdrawal_analytics,
                'balance_snapshot': balance_snapshot,
                'alerts': alerts,
                'user_role': request.user.role
            })
        except Exception as e:
            return Response(
                {'error': f'Erreur lors de la génération du résumé: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DashboardPreferencesViewSet(viewsets.ModelViewSet):
    """ViewSet pour les préférences du tableau de bord"""
    serializer_class = DashboardPreferencesSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return DashboardPreferences.objects.filter(user=self.request.user)

    def create(self, request, *args, **kwargs):
        # Vérifier si l'utilisateur a déjà des préférences
        if DashboardPreferences.objects.filter(user=request.user).exists():
            return Response(
                {'error': 'Des préférences existent déjà pour cet utilisateur'},
                status=status.HTTP_400_BAD_REQUEST
            )

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(user=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(
            instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)
        return Response(serializer.data)


class DashboardAlertViewSet(viewsets.ModelViewSet):
    """ViewSet pour les alertes du tableau de bord"""
    serializer_class = DashboardAlertSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if self.request.user.role == 'admin':
            return DashboardAlert.objects.all().order_by('-created_at')
        return DashboardAlert.objects.filter(
            Q(user=self.request.user) | Q(user__isnull=True)
        ).order_by('-created_at')

    def create(self, request, *args, **kwargs):
        if request.user.role != 'admin':
            return Response(
                {'error': 'Seul un administrateur peut créer des alertes'},
                status=status.HTTP_403_FORBIDDEN
            )
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        if request.user.role != 'admin':
            return Response(
                {'error': 'Seul un administrateur peut modifier des alertes'},
                status=status.HTTP_403_FORBIDDEN
            )
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        if request.user.role != 'admin':
            return Response(
                {'error': 'Seul un administrateur peut supprimer des alertes'},
                status=status.HTTP_403_FORBIDDEN
            )
        return super().destroy(request, *args, **kwargs)


class DashboardWidgetViewSet(viewsets.ModelViewSet):
    """ViewSet pour les widgets du tableau de bord"""
    serializer_class = DashboardWidgetSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if self.request.user.role == 'admin':
            return DashboardWidget.objects.all()
        return DashboardWidget.objects.filter(is_system=True)

    def create(self, request, *args, **kwargs):
        if request.user.role != 'admin':
            return Response(
                {'error': 'Seul un administrateur peut créer des widgets'},
                status=status.HTTP_403_FORBIDDEN
            )
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        if request.user.role != 'admin':
            return Response(
                {'error': 'Seul un administrateur peut modifier des widgets'},
                status=status.HTTP_403_FORBIDDEN
            )
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.is_system:
            return Response(
                {'error': 'Les widgets système ne peuvent pas être supprimés'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if request.user.role != 'admin':
            return Response(
                {'error': 'Seul un administrateur peut supprimer des widgets'},
                status=status.HTTP_403_FORBIDDEN
            )
        return super().destroy(request, *args, **kwargs)


class DashboardMetricViewSet(viewsets.ReadOnlyModelViewSet):
    """ViewSet pour les métriques du tableau de bord"""
    serializer_class = DashboardMetricSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if self.request.user.role == 'admin':
            return DashboardMetric.objects.all().order_by('-date')
        # Limité pour les agents
        return DashboardMetric.objects.filter().order_by('-date')[:10]

    @action(detail=False, methods=['post'], url_path='refresh')
    def refresh_metrics(self, request):
        """Rafraîchir les métriques"""
        if request.user.role != 'admin':
            return Response(
                {'error': 'Seul un administrateur peut rafraîchir les métriques'},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            # Mettre à jour les métriques (implémentation à faire)
            from .models import DashboardMetric

            stats = DashboardService.get_global_stats('last_30_days')

            # Créer ou mettre à jour les métriques
            metrics_data = [
                ('total_balance', stats['global_account']['balance']),
                ('total_transactions', stats['transactions']['total']),
                ('active_agents', stats['agents']['active']),
                ('total_partners', stats['partners']['total']),
                ('withdrawal_volume',
                 stats['transactions']['withdrawals_amount']),
                ('deposit_volume', stats['transactions']['deposits_amount']),
            ]

            updated = []
            for metric_type, value in metrics_data:
                metric, created = DashboardMetric.objects.update_or_create(
                    metric_type=metric_type,
                    defaults={'value': value}
                )
                updated.append({
                    'metric_type': metric_type,
                    'value': float(value),
                    'created': created
                })

            return Response({
                'message': 'Métriques mises à jour avec succès',
                'updated': updated
            })
        except Exception as e:
            return Response(
                {'error': f'Erreur lors du rafraîchissement des métriques: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
