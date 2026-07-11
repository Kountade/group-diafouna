# finance/serializers.py
from rest_framework import serializers
from .models import Partner, Account, Transaction, WithdrawalRecipient


class PartnerSerializer(serializers.ModelSerializer):
    class Meta:
        model = Partner
        fields = '__all__'


class AccountSerializer(serializers.ModelSerializer):
    owner_name = serializers.SerializerMethodField()
    owner_email = serializers.SerializerMethodField()

    class Meta:
        model = Account
        fields = ['id', 'account_type', 'balance',
                  'currency', 'owner_name', 'owner_email', 'created_at']

    def get_owner_name(self, obj):
        if obj.account_type == 'partner' and obj.partner:
            return obj.partner.name
        if obj.account_type == 'agent' and obj.user:
            return obj.user.get_full_name() or obj.user.email
        return "Compte Global"

    def get_owner_email(self, obj):
        if obj.account_type == 'partner' and obj.partner:
            return obj.partner.email
        if obj.account_type == 'agent' and obj.user:
            return obj.user.email
        return None


class WithdrawalRecipientSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source='full_name', read_only=True)

    class Meta:
        model = WithdrawalRecipient
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')


class WithdrawalRecipientSimpleSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source='full_name', read_only=True)

    class Meta:
        model = WithdrawalRecipient
        fields = ['id', 'first_name', 'last_name',
                  'full_name', 'phone', 'document_number']


class TransactionSerializer(serializers.ModelSerializer):
    from_account_type = serializers.CharField(
        source='from_account.account_type')
    to_account_type = serializers.CharField(source='to_account.account_type')
    created_by_email = serializers.EmailField(
        source='created_by.email', read_only=True)

    recipient_name = serializers.CharField(
        source='recipient.full_name', read_only=True)
    recipient_phone = serializers.CharField(
        source='recipient.phone', read_only=True)
    recipient_document = serializers.CharField(
        source='recipient.document_number', read_only=True)

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


class TransferBetweenAgentsSerializer(serializers.Serializer):
    """
    Sérialiseur pour le transfert entre agents
    """
    agent_destinataire_id = serializers.IntegerField(
        help_text="ID de l'agent destinataire"
    )
    amount = serializers.DecimalField(
        max_digits=15,
        decimal_places=2,
        min_value=0.01,
        help_text="Montant à transférer"
    )
    description = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Description du transfert"
    )
    motif = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Motif du transfert"
    )

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Le montant doit être positif")
        return value

    def validate_agent_destinataire_id(self, value):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        try:
            user = User.objects.get(id=value, role='agent')
        except User.DoesNotExist:
            raise serializers.ValidationError("Agent destinataire non trouvé")
        return value


class WithdrawalSerializer(serializers.Serializer):
    partner_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=15, decimal_places=2)
    description = serializers.CharField(required=False, allow_blank=True)

    recipient_id = serializers.IntegerField(
        required=False, help_text="ID du bénéficiaire existant")
    recipient_first_name = serializers.CharField(
        required=False, max_length=100)
    recipient_last_name = serializers.CharField(required=False, max_length=100)
    recipient_phone = serializers.CharField(required=False, max_length=20)
    recipient_email = serializers.EmailField(required=False, allow_blank=True)
    recipient_document_type = serializers.ChoiceField(
        choices=WithdrawalRecipient.DOCUMENT_TYPES, required=False
    )
    recipient_document_number = serializers.CharField(
        required=False, max_length=50)
    recipient_address = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):
        if data.get('recipient_id'):
            return data

        required_fields = ['recipient_first_name', 'recipient_last_name',
                           'recipient_phone', 'recipient_document_number']
        missing_fields = [
            field for field in required_fields if not data.get(field)]

        if missing_fields:
            raise serializers.ValidationError(
                f"Pour créer un nouveau bénéficiaire, les champs suivants sont requis: {', '.join(missing_fields)}"
            )

        return data
