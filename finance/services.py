from decimal import Decimal
from django.db import transaction as db_transaction
from django.core.exceptions import ValidationError
from .models import Account, Transaction, Partner, WithdrawalRecipient
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
    def deposit_partner(partner, amount, description="", created_by=None):
        """
        Dépôt partenaire : crédite compte partenaire + compte global.
        ✅ created_by permet d'enregistrer l'utilisateur qui effectue le dépôt.
        """
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
            from_account=global_acc,
            to_account=partner_acc,
            amount=amount,
            description=description,
            created_by=created_by,  # ✅ maintenant pris en compte
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
    def withdraw_partner_via_agent(partner, agent_user, amount, description="", recipient_data=None):
        """
        Retrait partenaire chez un agent.
        Le compte partenaire est débité, le compte agent est débité,
        le compte global n'est pas modifié.
        """
        if amount <= 0:
            raise ValidationError("Le montant doit être positif.")

        partner_acc = FinanceService.get_or_create_partner_account(partner)
        agent_acc = FinanceService.get_or_create_agent_account(agent_user)

        if partner_acc.balance < amount:
            raise ValidationError(
                f"Solde partenaire insuffisant. Solde actuel: {partner_acc.balance} {partner_acc.currency}"
            )
        if agent_acc.balance < amount:
            raise ValidationError(
                f"Solde agent insuffisant. Solde actuel: {agent_acc.balance} {agent_acc.currency}"
            )

        partner_acc.balance -= amount
        agent_acc.balance -= amount
        partner_acc.save()
        agent_acc.save()

        recipient = None
        recipient_name = None
        recipient_phone = None

        if recipient_data:
            recipient_id = recipient_data.get('recipient_id')
            if recipient_id:
                try:
                    recipient = WithdrawalRecipient.objects.get(
                        id=recipient_id)
                    recipient_name = recipient.full_name
                    recipient_phone = recipient.phone
                except WithdrawalRecipient.DoesNotExist:
                    raise ValidationError("Bénéficiaire non trouvé.")
            else:
                recipient = WithdrawalRecipient.objects.create(
                    first_name=recipient_data.get('recipient_first_name'),
                    last_name=recipient_data.get('recipient_last_name'),
                    email=recipient_data.get('recipient_email', ''),
                    phone=recipient_data.get('recipient_phone'),
                    document_type=recipient_data.get(
                        'recipient_document_type', 'cni'),
                    document_number=recipient_data.get(
                        'recipient_document_number'),
                    address=recipient_data.get('recipient_address', ''),
                )
                recipient_name = recipient.full_name
                recipient_phone = recipient.phone

        transaction = Transaction.objects.create(
            transaction_type='withdrawal',
            from_account=partner_acc,
            to_account=agent_acc,
            amount=amount,
            description=description,
            created_by=agent_user,
            recipient=recipient,
            recipient_name=recipient_name,
            recipient_phone=recipient_phone,
        )

        return partner_acc.balance, transaction

    @staticmethod
    @db_transaction.atomic
    def get_partner_balance(partner):
        account = Account.objects.filter(
            partner=partner, account_type='partner').first()
        return account.balance if account else Decimal('0.00')

    @staticmethod
    @db_transaction.atomic
    def get_agent_balance(agent_user):
        account = Account.objects.filter(
            user=agent_user, account_type='agent').first()
        return account.balance if account else Decimal('0.00')
    
    
    