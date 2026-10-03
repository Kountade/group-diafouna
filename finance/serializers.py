# finances/serializers.py
from rest_framework import serializers
from .models import Partner, Account, Transaction, WithdrawalRecipient


class PartnerSerializer(serializers.ModelSerializer):
    account_balance = serializers.SerializerMethodField()

    class Meta:
        model = Partner
        fields = '__all__'
        read_only_fields = ('is_deleted', 'deleted_at')

    def get_account_balance(self, obj):
        account = Account.objects.filter(
            partner=obj, account_type='partner').first()
        return float(account.balance) if account else 0.0


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
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = WithdrawalRecipient
        fields = '__all__'
        read_only_fields = ('created_at', 'updated_at')


class WithdrawalRecipientSimpleSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = WithdrawalRecipient
        fields = ['id', 'first_name', 'last_name',
                  'full_name', 'phone', 'document_number']


class TransactionSerializer(serializers.ModelSerializer):
    from_account_type = serializers.SerializerMethodField()
    to_account_type = serializers.SerializerMethodField()
    from_account_label = serializers.SerializerMethodField()
    to_account_label = serializers.SerializerMethodField()
    created_by_email = serializers.EmailField(
        source='created_by.email', read_only=True)
    created_by_full_name = serializers.CharField(
        source='created_by.get_full_name', read_only=True)
    recipient_name = serializers.CharField(
        source='recipient.full_name', read_only=True)
    recipient_phone = serializers.CharField(
        source='recipient.phone', read_only=True)
    recipient_document = serializers.CharField(
        source='recipient.document_number', read_only=True)
    partner_name = serializers.SerializerMethodField()
    movement_type = serializers.SerializerMethodField()
    movement_label = serializers.SerializerMethodField()

    # ✅ Champs calculés selon l'utilisateur courant
    is_reversible = serializers.SerializerMethodField()
    reverse_block_reason = serializers.SerializerMethodField()

    class Meta:
        model = Transaction
        fields = '__all__'

    def get_from_account_type(self, obj):
        if obj.from_account:
            return obj.from_account.account_type
        return obj.from_account_type_snapshot or 'deleted'

    def get_to_account_type(self, obj):
        if obj.to_account:
            return obj.to_account.account_type
        return obj.to_account_type_snapshot or 'deleted'

    def get_from_account_label(self, obj):
        if obj.from_account:
            return str(obj.from_account)
        return obj.from_account_label_snapshot or 'Compte supprimé'

    def get_to_account_label(self, obj):
        if obj.to_account:
            return str(obj.to_account)
        return obj.to_account_label_snapshot or 'Compte supprimé'

    def get_partner_name(self, obj):
        if obj.transaction_type == 'deposit' and obj.to_account and obj.to_account.account_type == 'partner':
            return obj.to_account.partner.name if obj.to_account.partner else None
        if obj.transaction_type == 'withdrawal' and obj.from_account and obj.from_account.account_type == 'partner':
            return obj.from_account.partner.name if obj.from_account.partner else None
        return None

    def get_movement_type(self, obj):
        if obj.transaction_type == 'deposit':
            return 'ENTREE'
        elif obj.transaction_type == 'withdrawal':
            return 'SORTIE'
        elif obj.transaction_type in ('transfer_to_agent', 'transfer_between_agents'):
            return 'TRANSFERT'
        elif obj.transaction_type in ('reversal', 'partner_deletion_reversal'):
            return 'ANNULATION'
        return obj.transaction_type.upper()

    def get_movement_label(self, obj):
        if obj.transaction_type == 'deposit':
            return 'ENTRÉE'
        elif obj.transaction_type == 'withdrawal':
            return 'SORTIE'
        elif obj.transaction_type == 'transfer_to_agent':
            return 'Transfert vers Agent'
        elif obj.transaction_type == 'transfer_between_agents':
            return 'Transfert entre Agents'
        elif obj.transaction_type == 'reversal':
            return 'Annulation de transaction'
        elif obj.transaction_type == 'partner_deletion_reversal':
            return 'Annulation (suppression partenaire)'
        return obj.get_transaction_type_display()

    # ────────────────────────────────────────────────────────────
    # ✅ Permissions calculées côté backend
    # ────────────────────────────────────────────────────────────

    def _get_agent_account(self, user):
        """Compte de l'agent connecté (ou None)."""
        if not user or not user.is_authenticated:
            return None
        if getattr(user, 'role', None) != 'agent':
            return None
        return Account.objects.filter(
            user=user, account_type='agent').first()

    def get_is_reversible(self, obj):
        """Indique si l'utilisateur courant peut annuler cette transaction."""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False

        user = request.user

        # Déjà annulée
        if obj.is_reversed:
            return False
        # Type non annulable
        if obj.transaction_type in Transaction.NON_REVERSIBLE_TYPES:
            return False
        # Comptes supprimés
        if obj.from_account is None and obj.to_account is None:
            return False

        # Admin : peut tout annuler
        if getattr(user, 'role', None) == 'admin':
            return True

        # Agent : seulement si son compte est impliqué
        if getattr(user, 'role', None) == 'agent':
            agent_account = self._get_agent_account(user)
            if not agent_account:
                return False
            return (
                obj.from_account_id == agent_account.id or
                obj.to_account_id == agent_account.id
            )

        return False

    def get_reverse_block_reason(self, obj):
        """Explique pourquoi l'annulation est bloquée (pour tooltip frontend)."""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return "Non authentifié"

        user = request.user

        if obj.is_reversed:
            return "Transaction déjà annulée"
        if obj.transaction_type in Transaction.NON_REVERSIBLE_TYPES:
            return "Une annulation ne peut pas être annulée"
        if obj.from_account is None and obj.to_account is None:
            return "Comptes liés supprimés"

        if getattr(user, 'role', None) == 'admin':
            return None

        if getattr(user, 'role', None) == 'agent':
            agent_account = self._get_agent_account(user)
            if not agent_account:
                return "Aucun compte agent associé"
            is_involved = (
                obj.from_account_id == agent_account.id or
                obj.to_account_id == agent_account.id
            )
            if not is_involved:
                return "Vous ne pouvez annuler que vos propres transactions"
            return None

        return "Non autorisé"


class DepositSerializer(serializers.Serializer):
    partner_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=15, decimal_places=2)
    description = serializers.CharField(required=False, allow_blank=True)


class TransferToAgentSerializer(serializers.Serializer):
    agent_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=15, decimal_places=2)
    description = serializers.CharField(required=False, allow_blank=True)


class TransferBetweenAgentsSerializer(serializers.Serializer):
    agent_destinataire_id = serializers.IntegerField()
    amount = serializers.DecimalField(
        max_digits=15, decimal_places=2, min_value=0.01)
    description = serializers.CharField(required=False, allow_blank=True)
    motif = serializers.CharField(required=False, allow_blank=True)

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError("Le montant doit être positif")
        return value

    def validate_agent_destinataire_id(self, value):
        from django.contrib.auth import get_user_model
        User = get_user_model()
        try:
            User.objects.get(id=value, role='agent')
        except User.DoesNotExist:
            raise serializers.ValidationError("Agent destinataire non trouvé")
        return value


class WithdrawalSerializer(serializers.Serializer):
    partner_id = serializers.IntegerField()
    amount = serializers.DecimalField(max_digits=15, decimal_places=2)
    description = serializers.CharField(required=False, allow_blank=True)

    recipient_id = serializers.IntegerField(required=False)
    recipient_first_name = serializers.CharField(
        required=False, max_length=100)
    recipient_last_name = serializers.CharField(required=False, max_length=100)
    recipient_phone = serializers.CharField(required=False, max_length=20)
    recipient_email = serializers.EmailField(required=False, allow_blank=True)
    recipient_document_type = serializers.ChoiceField(
        choices=WithdrawalRecipient.DOCUMENT_TYPES, required=False)
    recipient_document_number = serializers.CharField(
        required=False, max_length=50)
    recipient_address = serializers.CharField(required=False, allow_blank=True)

    def validate(self, data):
        if data.get('recipient_id'):
            return data
        required_fields = ['recipient_first_name', 'recipient_last_name',
                           'recipient_phone', 'recipient_document_number']
        missing_fields = [f for f in required_fields if not data.get(f)]
        if missing_fields:
            raise serializers.ValidationError(
                f"Champs requis pour nouveau bénéficiaire: {', '.join(missing_fields)}")
        return data


class PartnerDeletionSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True)
    confirm = serializers.BooleanField(required=True)
    hard_delete = serializers.BooleanField(required=False, default=False)

    def validate_confirm(self, value):
        if not value:
            raise serializers.ValidationError(
                "Vous devez confirmer la suppression en mettant 'confirm' à true.")
        return value
