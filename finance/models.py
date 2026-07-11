# finance/models.py
from django.db import models
from django.core.validators import MinValueValidator
from decimal import Decimal
from django.contrib.auth import get_user_model

User = get_user_model()

class Partner(models.Model):
    """Partenaire externe (pas de login)"""
    name = models.CharField(max_length=255)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return self.name

class Account(models.Model):
    ACCOUNT_TYPES = (
        ('global', 'Compte Global Entreprise'),
        ('partner', 'Compte Partenaire'),
        ('agent', 'Compte Agent'),
    )
    
    account_type = models.CharField(max_length=10, choices=ACCOUNT_TYPES)
    
    partner = models.OneToOneField(Partner, on_delete=models.CASCADE, null=True, blank=True)
    user = models.OneToOneField(User, on_delete=models.CASCADE, null=True, blank=True,
                                limit_choices_to={'role': 'agent'})
    
    balance = models.DecimalField(max_digits=15, decimal_places=2, default=Decimal('0.00'))
    currency = models.CharField(max_length=3, default='XOF')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    (models.Q(account_type='global') & models.Q(partner__isnull=True) & models.Q(user__isnull=True)) |
                    (models.Q(account_type='partner') & models.Q(partner__isnull=False) & models.Q(user__isnull=True)) |
                    (models.Q(account_type='agent') & models.Q(partner__isnull=True) & models.Q(user__isnull=False))
                ),
                name='valid_account_ownership'
            )
        ]

    def __str__(self):
        if self.account_type == 'global':
            return "Compte Global"
        if self.account_type == 'partner':
            return f"Compte Partenaire - {self.partner.name}"
        return f"Compte Agent - {self.user.email}"


class WithdrawalRecipient(models.Model):
    """
    Personne qui récupère l'argent lors d'un retrait partenaire
    """
    DOCUMENT_TYPES = (
        ('cni', 'Carte Nationale d\'Identité'),
        ('passport', 'Passeport'),
        ('permis', 'Permis de Conduire'),
        ('carte_sejour', 'Carte de Séjour'),
        ('autre', 'Autre'),
    )
    
    first_name = models.CharField(max_length=100, verbose_name="Prénom")
    last_name = models.CharField(max_length=100, verbose_name="Nom")
    email = models.EmailField(max_length=200, blank=True, null=True, verbose_name="Email")
    phone = models.CharField(max_length=20, verbose_name="Téléphone")
    document_type = models.CharField(max_length=20, choices=DOCUMENT_TYPES, default='cni', verbose_name="Type de pièce")
    document_number = models.CharField(max_length=50, unique=True, verbose_name="Numéro de pièce")
    address = models.TextField(blank=True, null=True, verbose_name="Adresse")
    
    # Informations supplémentaires
    is_regular = models.BooleanField(default=True, verbose_name="Client régulier")
    notes = models.TextField(blank=True, null=True, verbose_name="Notes")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = "Bénéficiaire de retrait"
        verbose_name_plural = "Bénéficiaires de retraits"
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.first_name} {self.last_name} - {self.phone}"
    
    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()


class Transaction(models.Model):
    TRANSACTION_TYPES = (
        ('deposit', 'Dépôt partenaire'),
        ('transfer_to_agent', 'Transfert Global → Agent'),
        ('withdrawal', 'Retrait partenaire via agent'),
    )
    
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    from_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='outgoing_transactions')
    to_account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name='incoming_transactions')
    amount = models.DecimalField(max_digits=15, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='transactions_created')
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Nouveaux champs pour les retraits
    recipient = models.ForeignKey(WithdrawalRecipient, on_delete=models.SET_NULL, null=True, blank=True, 
                                  related_name='transactions', verbose_name="Bénéficiaire du retrait")
    recipient_phone = models.CharField(max_length=20, blank=True, null=True, verbose_name="Téléphone du bénéficiaire")
    recipient_name = models.CharField(max_length=200, blank=True, null=True, verbose_name="Nom du bénéficiaire")

    def __str__(self):
        return f"{self.get_transaction_type_display()} - {self.amount} - {self.created_at}"