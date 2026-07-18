from rest_framework import serializers
from .models import DashboardPreferences, DashboardAlert, DashboardWidget, DashboardMetric
from django.contrib.auth import get_user_model

User = get_user_model()


class DashboardPreferencesSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(source='user.email', read_only=True)
    user_name = serializers.CharField(
        source='user.get_full_name', read_only=True)

    class Meta:
        model = DashboardPreferences
        fields = '__all__'
        read_only_fields = ('user', 'created_at', 'updated_at')


class DashboardAlertSerializer(serializers.ModelSerializer):
    user_email = serializers.EmailField(
        source='user.email', read_only=True, allow_null=True)

    class Meta:
        model = DashboardAlert
        fields = '__all__'
        read_only_fields = ('created_at',)


class DashboardWidgetSerializer(serializers.ModelSerializer):
    class Meta:
        model = DashboardWidget
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')


class DashboardMetricSerializer(serializers.ModelSerializer):
    class Meta:
        model = DashboardMetric
        fields = '__all__'
        read_only_fields = ('calculated_at',)


# Serializers pour les données de statistiques
class GlobalStatsSerializer(serializers.Serializer):
    partners = serializers.DictField()
    agents = serializers.DictField()
    global_account = serializers.DictField()
    transactions = serializers.DictField()
    period = serializers.DictField()


class TrendDataSerializer(serializers.Serializer):
    daily = serializers.ListField(child=serializers.DictField())
    moving_average_7d = serializers.ListField(child=serializers.DictField())
    total_days = serializers.IntegerField()


class TopPerformersSerializer(serializers.Serializer):
    top_partners = serializers.ListField(child=serializers.DictField())
    top_agents = serializers.ListField(child=serializers.DictField())


class WithdrawalAnalyticsSerializer(serializers.Serializer):
    total_withdrawals = serializers.IntegerField()
    total_amount = serializers.FloatField()
    avg_amount = serializers.FloatField()
    top_recipients = serializers.ListField(child=serializers.DictField())
    amount_distribution = serializers.DictField()
    period = serializers.DictField()


class BalanceSnapshotSerializer(serializers.Serializer):
    partner_accounts = serializers.DictField()
    agent_accounts = serializers.DictField()
