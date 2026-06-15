from decimal import Decimal
from django.db import transaction as db_transaction
from django.core.exceptions import ValidationError
from .models import Account, Transaction, Partner
from django.contrib.auth import get_user_model

User = get_user_model()


class FinanceService:

    @staticmethod
    @db_transaction.atomic
    def get_global_account():
        return Account.objects.get_or_create(account_type='global')[0]

    @staticmethod
    @db_transaction.atomic
    def get_or_create_partner_account(partner):
        account, _ = Account.objects.get_or_create(
            partner=partner, account_type='partner')
        return account

    @staticmethod
    @db_transaction.atomic
    def get_or_create_agent_account(agent_user):
        if agent_user.role != 'agent':
            raise ValidationError("Seul un agent peut avoir un compte.")
        account, _ = Account.objects.get_or_create(
            user=agent_user, account_type='agent')
        return account

    @staticmethod
    @db_transaction.atomic
    def deposit_partner(partner, amount, description=""):
        """Dépôt partenaire : crédite compte partenaire + compte global."""
        if amount <= 0:
            raise ValidationError("Le montant doit être positif.")

        partner_acc = FinanceService.get_or_create_partner_account(partner)
        global_acc = FinanceService.get_global_account()

        partner_acc.balance += amount
        global_acc.balance += amount
        partner_acc.save()
        global_acc.save()

        Transaction.objects.create(
            transaction_type='deposit',
            from_account=global_acc,   # origine fictive, argent extérieur
            to_account=partner_acc,
            amount=amount,
            description=description,
            created_by=None   # pourrait être un admin
        )
        return partner_acc.balance

    @staticmethod
    @db_transaction.atomic
    def transfer_to_agent(agent_user, amount, description=""):
        """Transfert du compte global vers un agent."""
        if amount <= 0:
            raise ValidationError("Montant invalide.")
        global_acc = FinanceService.get_global_account()
        agent_acc = FinanceService.get_or_create_agent_account(agent_user)

        if global_acc.balance < amount:
            raise ValidationError("Solde global insuffisant.")

        global_acc.balance -= amount
        agent_acc.balance += amount
        global_acc.save()
        agent_acc.save()

        Transaction.objects.create(
            transaction_type='transfer_to_agent',
            from_account=global_acc,
            to_account=agent_acc,
            amount=amount,
            description=description,
            created_by=agent_user
        )
        return agent_acc.balance

    @staticmethod
    @db_transaction.atomic
    def withdraw_partner_via_agent(partner, agent_user, amount, description=""):
        """
        Retrait partenaire chez un agent.
        Débite le compte partenaire et le compte agent.
        Le compte global n'est pas modifié (l'argent avait déjà été transféré à l'agent).
        """
        if amount <= 0:
            raise ValidationError("Montant invalide.")

        partner_acc = FinanceService.get_or_create_partner_account(partner)
        agent_acc = FinanceService.get_or_create_agent_account(agent_user)

        if partner_acc.balance < amount:
            raise ValidationError("Solde partenaire insuffisant.")
        if agent_acc.balance < amount:
            raise ValidationError(
                "Solde agent insuffisant (l'agent n'a pas assez de liquidités).")

        partner_acc.balance -= amount
        agent_acc.balance -= amount
        partner_acc.save()
        agent_acc.save()

        Transaction.objects.create(
            transaction_type='withdrawal',
            from_account=partner_acc,
            to_account=agent_acc,
            amount=amount,
            description=description,
            created_by=agent_user
        )
        return partner_acc.balance

    # ----- Variante si vous voulez aussi débiter le compte global -----
    @staticmethod
    @db_transaction.atomic
    def withdraw_partner_via_agent_with_global_debit(partner, agent_user, amount, description=""):
        """Retrait qui débite aussi le compte global (selon l'énoncé original)"""
        if amount <= 0:
            raise ValidationError("Montant invalide.")

        partner_acc = FinanceService.get_or_create_partner_account(partner)
        global_acc = FinanceService.get_global_account()
        agent_acc = FinanceService.get_or_create_agent_account(agent_user)

        if partner_acc.balance < amount:
            raise ValidationError("Solde partenaire insuffisant.")
        if global_acc.balance < amount:
            raise ValidationError("Solde global insuffisant.")

        partner_acc.balance -= amount
        global_acc.balance -= amount
        # agent_acc n'est pas modifié (l'agent ne perd pas son argent)
        partner_acc.save()
        global_acc.save()

        Transaction.objects.create(
            transaction_type='withdrawal',
            from_account=partner_acc,
            to_account=global_acc,   # ou agent_acc selon votre logique
            amount=amount,
            description=description,
            created_by=agent_user
        )
        return partner_acc.balance
