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
    
    # Un compte appartient soit à un partenaire, soit à un agent, soit à personne (global)
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

    def __str__(self):
        return f"{self.get_transaction_type_display()} - {self.amount} - {self.created_at}"