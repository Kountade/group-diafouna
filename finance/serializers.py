# finances/serializers.py
from rest_framework import serializers
from django.contrib.auth import get_user_model

from .models import Partner, Account, Transaction, WithdrawalRecipient

User = get_user_model()


# ============================================================
# PARTENAIRE
# ============================================================

class PartnerSerializer(serializers.ModelSerializer):
    account_balance = serializers.SerializerMethodField()
    is_deleted = serializers.BooleanField(read_only=True)
    deleted_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = Partner
        fields = [
            'id', 'name', 'email', 'phone', 'address',
            'is_deleted', 'deleted_at',
            'created_at', 'updated_at',
            'account_balance',
        ]
        read_only_fields = (
            'is_deleted', 'deleted_at', 'created_at', 'updated_at'
        )

    def get_account_balance(self, obj):
        """Retourne le solde en string Decimal (précision financière)."""
        account = Account.objects.filter(
            partner=obj, account_type='partner').first()
        return str(account.balance) if account else "0.00"


# ============================================================
# COMPTE
# ============================================================

class AccountSerializer(serializers.ModelSerializer):
    owner_name = serializers.SerializerMethodField()
    owner_email = serializers.SerializerMethodField()
    owner_type = serializers.SerializerMethodField()
    is_negative = serializers.SerializerMethodField()

    class Meta:
        model = Account
        fields = [
            'id', 'account_type', 'balance', 'currency',
            'owner_name', 'owner_email', 'owner_type', 'is_negative',
            'created_at', 'updated_at',
        ]
        read_only_fields = fields

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

    def get_owner_type(self, obj):
        return obj.account_type

    def get_is_negative(self, obj):
        # Toujours False grâce à la contrainte BDD, mais utile en monitoring
        return obj.balance < 0


# ============================================================
# BÉNÉFICIAIRE DE RETRAIT
# ============================================================

class WithdrawalRecipientSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = WithdrawalRecipient
        fields = [
            'id', 'first_name', 'last_name', 'full_name',
            'email', 'phone', 'document_type', 'document_number',
            'address', 'is_regular', 'notes',
            'created_at', 'updated_at',
        ]
        read_only_fields = ('created_at', 'updated_at')

    def validate_document_number(self, value):
        """Unicité explicite (la BDD le fait déjà, mais message plus clair)."""
        qs = WithdrawalRecipient.objects.filter(document_number=value)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError(
                "Un bénéficiaire avec ce numéro de pièce existe déjà.")
        return value


class WithdrawalRecipientSimpleSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)

    class Meta:
        model = WithdrawalRecipient
        fields = ['id', 'first_name', 'last_name',
                  'full_name', 'phone', 'document_number']


# ============================================================
# TRANSACTION
# ============================================================

class TransactionSerializer(serializers.ModelSerializer):
    # ── Champs calculés (délèguent aux @property du modèle) ──
    from_account_type = serializers.CharField(read_only=True)
    to_account_type = serializers.CharField(read_only=True)
    from_account_label = serializers.CharField(read_only=True)
    to_account_label = serializers.CharField(read_only=True)
    movement_type = serializers.CharField(read_only=True)
    partner_name = serializers.CharField(read_only=True, allow_null=True)

    # ── Champs liés à created_by ──
    created_by_email = serializers.EmailField(
        source='created_by.email', read_only=True)
    created_by_full_name = serializers.CharField(
        source='created_by.get_full_name', read_only=True)

    # ── Champs liés au bénéficiaire ──
    recipient_full_name = serializers.CharField(
        source='recipient.full_name', read_only=True)
    recipient_phone_detail = serializers.CharField(
        source='recipient.phone', read_only=True)
    recipient_document = serializers.CharField(
        source='recipient.document_number', read_only=True)

    # ── Champs calculés selon l'utilisateur courant ──
    is_reversible = serializers.SerializerMethodField()
    reverse_block_reason = serializers.SerializerMethodField()
    movement_label = serializers.SerializerMethodField()

    class Meta:
        model = Transaction
        fields = [
            'id', 'transaction_type',
            'from_account', 'to_account',
            'from_account_type', 'to_account_type',
            'from_account_label', 'to_account_label',
            'amount', 'description',
            'created_by', 'created_by_email', 'created_by_full_name',
            'created_at',
            'recipient', 'recipient_full_name',
            'recipient_phone', 'recipient_phone_detail', 'recipient_name',
            'recipient_document',
            'is_reversed', 'reversed_at', 'reversal_transaction',
            'from_account_type_snapshot', 'to_account_type_snapshot',
            'from_account_label_snapshot', 'to_account_label_snapshot',
            'partner_name', 'movement_type', 'movement_label',
            'is_reversible', 'reverse_block_reason',
        ]
        read_only_fields = fields

    # ────────────────────────────────────────────────────────────
    # Libellés
    # ────────────────────────────────────────────────────────────

    def get_movement_label(self, obj):
        mapping = {
            'company_topup': 'Approvisionnement compte global',
            'deposit': 'ENTRÉE',
            'withdrawal': 'SORTIE',
            'transfer_to_agent': 'Transfert vers Agent',
            'transfer_between_agents': 'Transfert entre Agents',
            'reversal': 'Annulation de transaction',
            'partner_deletion_reversal': 'Annulation (suppression partenaire)',
        }
        return mapping.get(obj.transaction_type,
                           obj.get_transaction_type_display())

    # ────────────────────────────────────────────────────────────
    # Permissions calculées côté backend
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
        """
        Indique si l'utilisateur courant peut annuler cette transaction.
        - Admin : peut annuler TOUT (sauf déjà annulée / type non annulable).
        - Agent : SEULEMENT s'il est l'ÉMETTEUR (from_account).
        """
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False

        user = request.user

        # Règles métier pures (déléguées au modèle)
        if not obj.is_reversible:
            return False

        # Admin : tout ce qui est réversible l'est
        if getattr(user, 'role', None) == 'admin':
            return True

        # Agent : SEULEMENT s'il est l'émetteur
        if getattr(user, 'role', None) == 'agent':
            agent_account = self._get_agent_account(user)
            if not agent_account:
                return False
            return obj.from_account_id == agent_account.id

        return False

    def get_reverse_block_reason(self, obj):
        """Explique pourquoi l'annulation est bloquée (tooltip frontend)."""
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
            if obj.from_account_id != agent_account.id:
                return ("Vous ne pouvez annuler que les transactions "
                        "que vous avez émises")
            return None

        return "Non autorisé"


# ============================================================
# SERIALIZERS D'OPÉRATIONS
# ============================================================

class TopupSerializer(serializers.Serializer):
    """Approvisionnement du compte global (admin uniquement)."""
    amount = serializers.DecimalField(
        max_digits=15, decimal_places=2, min_value=0.01)
    description = serializers.CharField(required=False, allow_blank=True)

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "Le montant doit être strictement positif.")
        return value


class DepositSerializer(serializers.Serializer):
    partner_id = serializers.IntegerField()
    amount = serializers.DecimalField(
        max_digits=15, decimal_places=2, min_value=0.01)
    description = serializers.CharField(required=False, allow_blank=True)

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "Le montant doit être strictement positif.")
        return value

    def validate_partner_id(self, value):
        if not Partner.objects.filter(id=value, is_deleted=False).exists():
            raise serializers.ValidationError(
                "Partenaire non trouvé ou supprimé.")
        return value


class TransferToAgentSerializer(serializers.Serializer):
    agent_id = serializers.IntegerField()
    amount = serializers.DecimalField(
        max_digits=15, decimal_places=2, min_value=0.01)
    description = serializers.CharField(required=False, allow_blank=True)

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "Le montant doit être strictement positif.")
        return value

    def validate_agent_id(self, value):
        if not User.objects.filter(id=value, role='agent').exists():
            raise serializers.ValidationError("Agent non trouvé.")
        return value


class TransferBetweenAgentsSerializer(serializers.Serializer):
    agent_destinataire_id = serializers.IntegerField()
    amount = serializers.DecimalField(
        max_digits=15, decimal_places=2, min_value=0.01)
    description = serializers.CharField(required=False, allow_blank=True)
    motif = serializers.CharField(required=False, allow_blank=True)

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "Le montant doit être positif.")
        return value

    def validate_agent_destinataire_id(self, value):
        if not User.objects.filter(id=value, role='agent').exists():
            raise serializers.ValidationError("Agent destinataire non trouvé.")
        return value


class WithdrawalSerializer(serializers.Serializer):
    partner_id = serializers.IntegerField()
    amount = serializers.DecimalField(
        max_digits=15, decimal_places=2, min_value=0.01)
    description = serializers.CharField(required=False, allow_blank=True)

    # Bénéficiaire existant OU nouveau
    recipient_id = serializers.IntegerField(required=False)
    recipient_first_name = serializers.CharField(
        required=False, max_length=100)
    recipient_last_name = serializers.CharField(required=False, max_length=100)
    recipient_phone = serializers.CharField(
        required=False, max_length=20)
    recipient_email = serializers.EmailField(required=False, allow_blank=True)
    recipient_document_type = serializers.ChoiceField(
        choices=WithdrawalRecipient.DOCUMENT_TYPES, required=False)
    recipient_document_number = serializers.CharField(
        required=False, max_length=50)
    recipient_address = serializers.CharField(
        required=False, allow_blank=True)

    def validate_amount(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "Le montant doit être strictement positif.")
        return value

    def validate_partner_id(self, value):
        if not Partner.objects.filter(id=value, is_deleted=False).exists():
            raise serializers.ValidationError(
                "Partenaire non trouvé ou supprimé.")
        return value

    def validate(self, data):
        # Cas 1 : bénéficiaire existant
        if data.get('recipient_id'):
            if not WithdrawalRecipient.objects.filter(
                    id=data['recipient_id']).exists():
                raise serializers.ValidationError(
                    {"recipient_id": "Bénéficiaire non trouvé."})
            return data

        # Cas 2 : nouveau bénéficiaire → champs obligatoires
        required_fields = [
            'recipient_first_name', 'recipient_last_name',
            'recipient_phone', 'recipient_document_number',
        ]
        missing_fields = [
            f for f in required_fields if not data.get(f)
        ]
        if missing_fields:
            raise serializers.ValidationError(
                "Champs requis pour nouveau bénéficiaire : "
                + ", ".join(missing_fields)
            )

        # Unicité du document
        if WithdrawalRecipient.objects.filter(
                document_number=data['recipient_document_number']).exists():
            raise serializers.ValidationError({
                'recipient_document_number':
                    "Un bénéficiaire avec ce numéro de pièce existe déjà."
            })

        return data


class PartnerDeletionSerializer(serializers.Serializer):
    reason = serializers.CharField(required=False, allow_blank=True)
    confirm = serializers.BooleanField(required=True)
    hard_delete = serializers.BooleanField(required=False, default=False)

    def validate_confirm(self, value):
        if not value:
            raise serializers.ValidationError(
                "Vous devez confirmer la suppression en mettant "
                "'confirm' à true.")
        return value


class ReverseTransactionSerializer(serializers.Serializer):
    """Annulation d'une transaction."""
    reason = serializers.CharField(required=False, allow_blank=True)
