# finance/services.py
from decimal import Decimal
from django.db import transaction as db_transaction
from django.core.exceptions import ValidationError
from .models import Account, Transaction, Partner, WithdrawalRecipient
from django.contrib.auth import get_user_model
from django.db import models

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
        DÉPÔT Partenaire - CRÉDITE le compte partenaire et le compte global
        ✅ Type: 'deposit' → Sera affiché comme 'ENTRÉE'
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
            created_by=created_by,
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
    def transfer_between_agents(from_agent, to_agent, amount, description=""):
        """
        Transfert d'argent entre deux agents.
        """
        if amount <= 0:
            raise ValidationError("Le montant doit être positif.")

        if from_agent.id == to_agent.id:
            raise ValidationError(
                "Vous ne pouvez pas vous transférer de l'argent à vous-même.")

        from_acc = FinanceService.get_or_create_agent_account(from_agent)
        to_acc = FinanceService.get_or_create_agent_account(to_agent)

        if from_acc.balance < amount:
            raise ValidationError(
                f"Solde insuffisant. Solde actuel: {from_acc.balance} {from_acc.currency}"
            )

        from_acc.balance -= amount
        to_acc.balance += amount
        from_acc.save()
        to_acc.save()

        transaction = Transaction.objects.create(
            transaction_type='transfer_between_agents',
            from_account=from_acc,
            to_account=to_acc,
            amount=amount,
            description=description,
            created_by=from_agent
        )

        return from_acc.balance, transaction

    @staticmethod
    @db_transaction.atomic
    def withdraw_partner_via_agent(partner, agent_user, amount, description="", recipient_data=None):
        """
        RETRAIT Partenaire - DÉBITE le compte partenaire et le compte agent
        ✅ Type: 'withdrawal' → Sera affiché comme 'SORTIE'
        ✅ MODIFICATION: Suppression de la vérification du solde du partenaire
        ✅ Le partenaire peut maintenant avoir un solde négatif
        """
        if amount <= 0:
            raise ValidationError("Le montant doit être positif.")

        partner_acc = FinanceService.get_or_create_partner_account(partner)
        agent_acc = FinanceService.get_or_create_agent_account(agent_user)

        # ❌ SUPPRESSION de la vérification du solde du partenaire
        # Le partenaire peut maintenant avoir un solde négatif

        # Mise à jour des soldes
        partner_acc.balance -= amount  # ✅ Permet d'avoir un solde négatif
        agent_acc.balance -= amount    # ✅ Permet d'avoir un solde négatif

        partner_acc.save()
        agent_acc.save()

        # Gestion du bénéficiaire
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
                # Créer un nouveau bénéficiaire
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

        # Création de la transaction
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

    @staticmethod
    @db_transaction.atomic
    def get_global_balance():
        account = FinanceService.get_global_account()
        return account.balance

    @staticmethod
    @db_transaction.atomic
    def get_partner_transactions(partner, limit=100):
        """Récupère les transactions d'un partenaire."""
        partner_account = Account.objects.filter(
            partner=partner, account_type='partner'
        ).first()

        if not partner_account:
            return []

        transactions = Transaction.objects.filter(
            models.Q(from_account=partner_account) |
            models.Q(to_account=partner_account)
        ).order_by('-created_at')[:limit]

        return transactions

    @staticmethod
    @db_transaction.atomic
    def get_agent_transactions(agent_user, limit=100):
        """Récupère les transactions d'un agent."""
        agent_account = Account.objects.filter(
            user=agent_user, account_type='agent'
        ).first()

        if not agent_account:
            return []

        transactions = Transaction.objects.filter(
            models.Q(from_account=agent_account) |
            models.Q(to_account=agent_account)
        ).order_by('-created_at')[:limit]

        return transactions

    @staticmethod
    @db_transaction.atomic
    def get_withdrawal_recipient(recipient_id):
        """Récupère un bénéficiaire par son ID."""
        try:
            return WithdrawalRecipient.objects.get(id=recipient_id)
        except WithdrawalRecipient.DoesNotExist:
            return None

    @staticmethod
    @db_transaction.atomic
    def create_withdrawal_recipient(data):
        """Crée un nouveau bénéficiaire de retrait."""
        recipient = WithdrawalRecipient.objects.create(
            first_name=data.get('first_name'),
            last_name=data.get('last_name'),
            email=data.get('email', ''),
            phone=data.get('phone'),
            document_type=data.get('document_type', 'cni'),
            document_number=data.get('document_number'),
            address=data.get('address', ''),
            is_regular=data.get('is_regular', True),
            notes=data.get('notes', ''),
        )
        return recipient

    @staticmethod
    @db_transaction.atomic
    def get_withdrawal_stats(partner=None, agent=None, date_from=None, date_to=None):
        """
        Récupère les statistiques des retraits.
        """
        withdrawals = Transaction.objects.filter(transaction_type='withdrawal')

        if partner:
            partner_account = Account.objects.filter(
                partner=partner, account_type='partner'
            ).first()
            if partner_account:
                withdrawals = withdrawals.filter(
                    models.Q(from_account=partner_account)
                )

        if agent:
            agent_account = Account.objects.filter(
                user=agent, account_type='agent'
            ).first()
            if agent_account:
                withdrawals = withdrawals.filter(
                    models.Q(to_account=agent_account)
                )

        if date_from:
            withdrawals = withdrawals.filter(created_at__gte=date_from)
        if date_to:
            withdrawals = withdrawals.filter(created_at__lte=date_to)

        total_count = withdrawals.count()
        total_amount = withdrawals.aggregate(models.Sum('amount'))[
            'amount__sum'] or Decimal('0.00')

        return {
            'total_count': total_count,
            'total_amount': total_amount,
            'average_amount': total_amount / total_count if total_count > 0 else Decimal('0.00')
        }

    @staticmethod
    @db_transaction.atomic
    def get_system_stats():
        """
        Récupère les statistiques générales du système.
        """
        total_partners = Partner.objects.count()
        total_agents = User.objects.filter(role='agent').count()

        global_account = FinanceService.get_global_account()

        partner_accounts = Account.objects.filter(account_type='partner')
        agent_accounts = Account.objects.filter(account_type='agent')

        total_partner_balance = sum(acc.balance for acc in partner_accounts)
        total_agent_balance = sum(acc.balance for acc in agent_accounts)

        transactions = Transaction.objects.all()
        total_transactions = transactions.count()

        deposits = transactions.filter(transaction_type='deposit')
        transfers = transactions.filter(transaction_type='transfer_to_agent')
        withdrawals = transactions.filter(transaction_type='withdrawal')

        return {
            'partners': {
                'total': total_partners,
                'total_balance': total_partner_balance,
                'active': partner_accounts.filter(balance__gt=0).count()
            },
            'agents': {
                'total': total_agents,
                'total_balance': total_agent_balance,
                'active': agent_accounts.filter(balance__gt=0).count()
            },
            'global_account': {
                'balance': global_account.balance,
                'currency': global_account.currency
            },
            'transactions': {
                'total': total_transactions,
                'deposits': deposits.count(),
                'deposits_total': deposits.aggregate(models.Sum('amount'))['amount__sum'] or Decimal('0.00'),
                'transfers': transfers.count(),
                'transfers_total': transfers.aggregate(models.Sum('amount'))['amount__sum'] or Decimal('0.00'),
                'withdrawals': withdrawals.count(),
                'withdrawals_total': withdrawals.aggregate(models.Sum('amount'))['amount__sum'] or Decimal('0.00'),
            }
        }
