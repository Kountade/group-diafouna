# finances/models.py
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.contrib.auth import get_user_model
from django.utils import timezone

User = get_user_model()


# ============================================================
# PARTENAIRE
# ============================================================

class Partner(models.Model):
    """
    Partenaire externe (pas de login).
    Suppression logique par défaut (soft delete) pour préserver
    l'historique comptable.
    """
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)

    is_deleted = models.BooleanField(default=False, verbose_name="Supprimé")
    deleted_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Date de suppression")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Partenaire"
        verbose_name_plural = "Partenaires"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['is_deleted']),
        ]

    def __str__(self):
        return self.name

    def delete(self, using=None, keep_parents=False):
        """Soft delete : marque le partenaire comme supprimé."""
        self.is_deleted = True
        self.deleted_at = timezone.now()
        self.save(update_fields=['is_deleted', 'deleted_at', 'updated_at'])

    def hard_delete(self, using=None, keep_parents=False):
        """Suppression physique réelle (irréversible)."""
        super().delete(using=using, keep_parents=keep_parents)


# ============================================================
# COMPTE
# ============================================================

class Account(models.Model):
    """
    Compte financier. Trois types :
      - global  : trésor de l'entreprise (JAMAIS négatif)
      - partner : compte d'un partenaire (peut être négatif en cas de retrait)
      - agent   : compte d'un agent (peut être négatif en cas de retrait)

    INVARIANTS :
      - Global   : solde >= 0 TOUJOURS
      - Partner  : solde >= 0 SAUF lors d'un retrait
                   (le partenaire peut retirer plus que son solde,
                   il comble son découvert par un futur dépôt)
      - Agent    : solde >= 0 SAUF lors d'un retrait
                   (l'agent avance le cash au bénéficiaire)

    Garanties à deux niveaux :
      1. FinanceService : _debit() vérifie (Global, transferts agent→agent,
                          annulations) ; _debit_allow_negative() n'est
                          autorisé QUE pour partner et agent, dans le
                          cadre strict d'un retrait.
      2. Base de données : contrainte balance_non_negative_except_partner_and_agent.
    """
    ACCOUNT_TYPES = (
        ('global', 'Compte Global Entreprise'),
        ('partner', 'Compte Partenaire'),
        ('agent', 'Compte Agent'),
    )

    account_type = models.CharField(max_length=10, choices=ACCOUNT_TYPES)
    partner = models.OneToOneField(
        Partner, on_delete=models.CASCADE, null=True, blank=True,
        related_name='account')
    user = models.OneToOneField(
        User, on_delete=models.CASCADE, null=True, blank=True,
        limit_choices_to={'role': 'agent'},
        related_name='agent_account')

    balance = models.DecimalField(
        max_digits=15, decimal_places=2, default=Decimal('0.00'),
        # NOTE : MinValueValidator retiré — la contrainte BDD scopée
        # gère le contrôle, et partner/agent peuvent être négatifs.
        verbose_name="Solde")
    currency = models.CharField(max_length=3, default='XOF')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Compte"
        verbose_name_plural = "Comptes"
        ordering = ['account_type', '-created_at']
        constraints = [
            # Cohérence type de compte / propriétaire
            models.CheckConstraint(
                check=(
                    (models.Q(account_type='global')
                     & models.Q(partner__isnull=True)
                     & models.Q(user__isnull=True))
                    |
                    (models.Q(account_type='partner')
                     & models.Q(partner__isnull=False)
                     & models.Q(user__isnull=True))
                    |
                    (models.Q(account_type='agent')
                     & models.Q(partner__isnull=True)
                     & models.Q(user__isnull=False))
                ),
                name='valid_account_ownership'
            ),
            # ✅ Solde non négatif SAUF pour partner et agent.
            #    Le Global ne peut JAMAIS être négatif.
            models.CheckConstraint(
                check=(
                    models.Q(balance__gte=0)
                    | models.Q(account_type__in=['partner', 'agent'])
                ),
                name='balance_non_negative_except_partner_and_agent'
            ),
        ]
        indexes = [
            models.Index(fields=['account_type']),
            models.Index(fields=['account_type', 'balance']),
        ]

    def __str__(self):
        if self.account_type == 'global':
            return "Compte Global"
        if self.account_type == 'partner':
            partner_name = self.partner.name if self.partner else 'N/A'
            return f"Compte Partenaire - {partner_name}"
        user_email = self.user.email if self.user else 'N/A'
        return f"Compte Agent - {user_email}"

    def clean(self):
        """Validation métier (appelée par les forms/admin, pas par save())."""
        super().clean()
        # Un partenaire ne peut avoir qu'un seul compte partenaire
        if self.account_type == 'partner' and self.partner_id:
            existing = Account.objects.filter(
                partner_id=self.partner_id, account_type='partner'
            ).exclude(pk=self.pk).first()
            if existing:
                raise ValidationError(
                    "Ce partenaire possède déjà un compte partenaire.")
        # Un agent ne peut avoir qu'un seul compte agent
        if self.account_type == 'agent' and self.user_id:
            existing = Account.objects.filter(
                user_id=self.user_id, account_type='agent'
            ).exclude(pk=self.pk).first()
            if existing:
                raise ValidationError(
                    "Cet agent possède déjà un compte agent.")

    def save(self, *args, **kwargs):
        """
        Refuse un solde négatif SAUF pour un compte partner ou agent.
        (Double sécurité avec la contrainte BDD.)
        Le Global ne peut JAMAIS être négatif.
        """
        if (self.balance is not None
                and self.balance < 0
                and self.account_type == 'global'):
            raise ValidationError(
                f"Le solde d'un compte global ne peut pas être négatif "
                f"(tentative : {self.balance} {self.currency}).")
        super().save(*args, **kwargs)

    @property
    def is_in_overdraft(self):
        """True si le compte est en découvert (uniquement partner ou agent)."""
        return self.balance < 0


# ============================================================
# BÉNÉFICIAIRE DE RETRAIT
# ============================================================

class WithdrawalRecipient(models.Model):
    """Personne qui récupère l'argent lors d'un retrait partenaire."""
    DOCUMENT_TYPES = (
        ('cni', "Carte Nationale d'Identité"),
        ('passport', 'Passeport'),
        ('permis', 'Permis de Conduire'),
        ('carte_sejour', 'Carte de Séjour'),
        ('autre', 'Autre'),
    )

    first_name = models.CharField(max_length=100, verbose_name="Prénom")
    last_name = models.CharField(max_length=100, verbose_name="Nom")
    email = models.EmailField(
        max_length=200, blank=True, null=True, verbose_name="Email")
    phone = models.CharField(max_length=20, verbose_name="Téléphone")
    document_type = models.CharField(
        max_length=20, choices=DOCUMENT_TYPES, default='cni',
        verbose_name="Type de pièce")
    document_number = models.CharField(
        max_length=50, unique=True, verbose_name="Numéro de pièce")
    address = models.TextField(blank=True, null=True, verbose_name="Adresse")
    is_regular = models.BooleanField(
        default=True, verbose_name="Client régulier")
    notes = models.TextField(blank=True, null=True, verbose_name="Notes")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Bénéficiaire de retrait"
        verbose_name_plural = "Bénéficiaires de retraits"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['phone']),
            models.Index(fields=['document_number']),
        ]

    def __str__(self):
        return f"{self.first_name} {self.last_name} - {self.phone}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()


# ============================================================
# TRANSACTION
# ============================================================

class Transaction(models.Model):
    """
    Écriture comptable.

    Types :
      - company_topup          : crédit du Global (entrée externe)
      - deposit                : Partner + et Global + (entrée externe)
      - transfer_to_agent      : Global → Agent
      - withdrawal             : Partner → Agent
                                 (partner et agent peuvent passer négatifs)
      - transfer_between_agents: Agent → Agent
      - reversal               : annulation (inverse une transaction)
      - partner_deletion_reversal : annulation lors suppression partenaire
    """
    TRANSACTION_TYPES = (
        ('company_topup', 'Approvisionnement compte global'),
        ('deposit', 'ENTRÉE'),
        ('transfer_to_agent', 'Transfert vers Agent'),
        ('withdrawal', 'SORTIE'),
        ('transfer_between_agents', 'Transfert entre Agents'),
        ('reversal', 'Annulation de transaction'),
        ('partner_deletion_reversal', 'Annulation suppression partenaire'),
    )

    # ✅ Types non annulables (on n'annule pas une annulation)
    NON_REVERSIBLE_TYPES = ('reversal', 'partner_deletion_reversal')

    transaction_type = models.CharField(
        max_length=30, choices=TRANSACTION_TYPES, db_index=True)

    # SET_NULL : permet la suppression d'un compte sans casser l'historique
    from_account = models.ForeignKey(
        Account, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='outgoing_transactions',
        verbose_name="Compte source")
    to_account = models.ForeignKey(
        Account, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='incoming_transactions',
        verbose_name="Compte destination")

    amount = models.DecimalField(
        max_digits=15, decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))],
        verbose_name="Montant")
    description = models.TextField(blank=True)

    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transactions_created',
        verbose_name="Créé par")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    # Bénéficiaire (retraits uniquement)
    recipient = models.ForeignKey(
        WithdrawalRecipient, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='transactions', verbose_name="Bénéficiaire du retrait")
    recipient_phone = models.CharField(
        max_length=20, blank=True, null=True,
        verbose_name="Téléphone du bénéficiaire")
    recipient_name = models.CharField(
        max_length=200, blank=True, null=True,
        verbose_name="Nom du bénéficiaire")

    # Annulation
    is_reversed = models.BooleanField(default=False, db_index=True,
                                      verbose_name="Annulée")
    reversed_at = models.DateTimeField(
        null=True, blank=True, verbose_name="Date d'annulation")
    reversal_transaction = models.OneToOneField(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reversed_transaction',
        verbose_name="Transaction d'annulation")

    # Snapshots : conservés après suppression du compte lié
    from_account_type_snapshot = models.CharField(
        max_length=20, blank=True, default='')
    to_account_type_snapshot = models.CharField(
        max_length=20, blank=True, default='')
    from_account_label_snapshot = models.CharField(
        max_length=255, blank=True, default='')
    to_account_label_snapshot = models.CharField(
        max_length=255, blank=True, default='')

    class Meta:
        verbose_name = "Transaction"
        verbose_name_plural = "Transactions"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['transaction_type']),
            models.Index(fields=['is_reversed']),
            models.Index(fields=['created_at']),
            models.Index(fields=['transaction_type', 'is_reversed']),
        ]
        constraints = [
            # ✅ Montant strictement positif
            models.CheckConstraint(
                check=models.Q(amount__gt=0),
                name='transaction_amount_positive'
            ),
        ]

    def __str__(self):
        return (
            f"{self.get_transaction_type_display()} - "
            f"{self.amount} {self.created_at:%Y-%m-%d %H:%M}"
        )

    def save(self, *args, **kwargs):
        """Remplit automatiquement les snapshots à la création."""
        if self.from_account and not self.from_account_type_snapshot:
            self.from_account_type_snapshot = self.from_account.account_type
            self.from_account_label_snapshot = str(self.from_account)
        if self.to_account and not self.to_account_type_snapshot:
            self.to_account_type_snapshot = self.to_account.account_type
            self.to_account_label_snapshot = str(self.to_account)
        super().save(*args, **kwargs)

    # ── Helpers d'affichage ────────────────────────────────────

    @property
    def from_account_type(self):
        if self.from_account:
            return self.from_account.account_type
        return self.from_account_type_snapshot or 'deleted'

    @property
    def to_account_type(self):
        if self.to_account:
            return self.to_account.account_type
        return self.to_account_type_snapshot or 'deleted'

    @property
    def from_account_label(self):
        if self.from_account:
            return str(self.from_account)
        return self.from_account_label_snapshot or 'Compte supprimé'

    @property
    def to_account_label(self):
        if self.to_account:
            return str(self.to_account)
        return self.to_account_label_snapshot or 'Compte supprimé'

    @property
    def is_reversible(self):
        """Indique si la transaction peut être annulée (règle métier pure)."""
        return (
            not self.is_reversed
            and self.transaction_type not in self.NON_REVERSIBLE_TYPES
            and (self.from_account is not None or self.to_account is not None)
        )

    @property
    def movement_type(self):
        """Catégorie large : ENTRÉE, SORTIE, TRANSFERT, APPROVISIONNEMENT, ANNULATION."""
        if self.transaction_type == 'company_topup':
            return 'APPROVISIONNEMENT'
        if self.transaction_type == 'deposit':
            return 'ENTREE'
        if self.transaction_type == 'withdrawal':
            return 'SORTIE'
        if self.transaction_type in ('transfer_to_agent',
                                     'transfer_between_agents'):
            return 'TRANSFERT'
        if self.transaction_type in ('reversal', 'partner_deletion_reversal'):
            return 'ANNULATION'
        return self.transaction_type.upper()

    @property
    def partner_name(self):
        """Nom du partenaire concerné (dépôt ou retrait), sinon None."""
        if (self.transaction_type == 'deposit'
                and self.to_account
                and self.to_account.account_type == 'partner'
                and self.to_account.partner):
            return self.to_account.partner.name
        if (self.transaction_type == 'withdrawal'
                and self.from_account
                and self.from_account.account_type == 'partner'
                and self.from_account.partner):
            return self.from_account.partner.name
        return None
