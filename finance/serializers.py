from rest_framework import serializers
from .models import Partner, Account, Transaction


class PartnerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Partner
        fields = '__all__'


class AccountSerializer(serializers.ModelSerializer):
    owner_name = serializers.SerializerMethodField()

    class Meta:
        model = Account
        fields = ['id', 'account_type', 'balance',
                  'currency', 'owner_name', 'created_at']

    def get_owner_name(self, obj):
        if obj.account_type == 'partner' and obj.partner:
            return obj.partner.name
        if obj.account_type == 'agent' and obj.user:
            return obj.user.email
        return "Compte Global"


class TransactionSerializer(serializers.ModelSerializer):
    from_account_type = serializers.CharField(
        source='from_account.account_type')
    to_account_type = serializers.CharField(source='to_account.account_type')
    created_by_email = serializers.EmailField(
        source='created_by.email', read_only=True)

    class Meta:
        model = Transaction
        fields = '__all__'


class DepositSerializer(serializers.Serializer):
    partner_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=15, decimal_places=2)
    description = serializers.CharField(required=False, allow_blank=True)


class TransferToAgentSerializer(serializers.Serializer):
    agent_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=15, decimal_places=2)
    description = serializers.CharField(required=False, allow_blank=True)


class WithdrawalSerializer(serializers.Serializer):
    partner_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=15, decimal_places=2)
    description = serializers.CharField(required=False, allow_blank=True)
