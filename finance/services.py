# finances/services.py
from decimal import Decimal
from django.db import transaction as db_transaction
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.db import models
from django.contrib.auth import get_user_model

from .models import Account, Transaction, Partner, WithdrawalRecipient

User = get_user_model()


class FinanceService:

    # ============================================================
    # COMPTES
    # ============================================================

    @staticmethod
    @db_transaction.atomic
    def get_global_account():
        return Account.objects.get_or_create(
            account_type='global',
            defaults={'balance': Decimal('0.00'), 'currency': 'XOF'}
        )[0]

    @staticmethod
    @db_transaction.atomic
    def get_or_create_partner_account(partner):
        account, _ = Account.objects.get_or_create(
            partner=partner, account_type='partner',
            defaults={'balance': Decimal('0.00'), 'currency': 'XOF'})
        return account

    @staticmethod
    @db_transaction.atomic
    def get_or_create_agent_account(agent_user):
        if agent_user.role != 'agent':
            raise ValidationError("Seul un agent peut avoir un compte.")
        account, _ = Account.objects.get_or_create(
            user=agent_user, account_type='agent',
            defaults={'balance': Decimal('0.00'), 'currency': 'XOF'})
        return account

    # ============================================================
    # OPÉRATIONS FINANCIÈRES
    # ============================================================

    @staticmethod
    @db_transaction.atomic
    def deposit_partner(partner, amount, description="", created_by=None):
        """
        DÉPÔT Partenaire (ENTRÉE) :
        - Compte Global : DIMINUE (le global donne l'argent)
        - Compte Partenaire : AUGMENTE (le partenaire reçoit)
        """
        amount = Decimal(str(amount))
        if amount <= 0:
            raise ValidationError("Le montant doit être positif.")

        if partner.is_deleted:
            raise ValidationError("Ce partenaire est supprimé.")

        partner_acc = FinanceService.get_or_create_partner_account(partner)
        global_acc = FinanceService.get_global_account()

        # ✅ CORRECTION : Le global DIMINUE
        global_acc.balance -= amount
        partner_acc.balance += amount
        global_acc.save()
        partner_acc.save()

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
        """Transfert Global → Agent : Global DIMINUE, Agent AUGMENTE"""
        amount = Decimal(str(amount))
        if amount <= 0:
            raise ValidationError("Montant invalide.")

        global_acc = FinanceService.get_global_account()
        agent_acc = FinanceService.get_or_create_agent_account(agent_user)

        if global_acc.balance < amount:
            raise ValidationError(
                f"Solde global insuffisant. Solde actuel: {global_acc.balance} XOF")

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
        """Transfert Agent → Agent : source DIMINUE, destination AUGMENTE"""
        amount = Decimal(str(amount))
        if amount <= 0:
            raise ValidationError("Le montant doit être positif.")

        if from_agent.id == to_agent.id:
            raise ValidationError(
                "Vous ne pouvez pas vous transférer de l'argent à vous-même.")

        from_acc = FinanceService.get_or_create_agent_account(from_agent)
        to_acc = FinanceService.get_or_create_agent_account(to_agent)

        if from_acc.balance < amount:
            raise ValidationError(
                f"Solde insuffisant. Solde actuel: {from_acc.balance} {from_acc.currency}")

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
        RETRAIT Partenaire (SORTIE) :
        - Compte Partenaire : DIMINUE
        - Compte Agent : AUGMENTE (l'agent reçoit l'argent qu'il va donner au bénéficiaire)
        """
        amount = Decimal(str(amount))
        if amount <= 0:
            raise ValidationError("Le montant doit être positif.")

        if partner.is_deleted:
            raise ValidationError("Ce partenaire est supprimé.")

        partner_acc = FinanceService.get_or_create_partner_account(partner)
        agent_acc = FinanceService.get_or_create_agent_account(agent_user)

        # ✅ CORRECTION : L'agent AUGMENTE (il reçoit l'argent du partenaire)
        partner_acc.balance -= amount
        agent_acc.balance += amount
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

    # ============================================================
    # ANNULATION DE TRANSACTION
    # ============================================================

    @staticmethod
    @db_transaction.atomic
    def reverse_transaction(transaction, reversed_by=None, reason=""):
        """
        Annule une transaction : inverse les soldes des comptes concernés.
        """
        if transaction.is_reversed:
            raise ValidationError("Cette transaction a déjà été annulée.")

        from_account = transaction.from_account
        to_account = transaction.to_account
        amount = transaction.amount

        # Inverser : ce qui a été retiré est remis, ce qui a été ajouté est retiré
        if from_account:
            from_account.balance += amount
            from_account.save()
        if to_account:
            to_account.balance -= amount
            to_account.save()

        reversal = Transaction.objects.create(
            transaction_type='partner_deletion_reversal',
            from_account=to_account,
            to_account=from_account,
            amount=amount,
            description=f"Annulation: {reason or 'Annulation'} - Transaction #{transaction.id}",
            created_by=reversed_by,
        )

        transaction.is_reversed = True
        transaction.reversed_at = timezone.now()
        transaction.reversal_transaction = reversal
        transaction.save(update_fields=[
            'is_reversed', 'reversed_at', 'reversal_transaction'])

        return reversal

    # ============================================================
    # SUPPRESSION PARTENAIRE (LOGIQUE CORRIGÉE)
    # ============================================================

    @staticmethod
    @db_transaction.atomic
    def delete_partner_and_reverse(partner, deleted_by=None, reason="", hard=False):
        """
        Supprime un partenaire et RESTAURE les soldes correctement.

        LOGIQUE FINANCIÈRE :
        --------------------
        Le solde du partenaire = somme des dépôts reçus - somme des retraits effectués.
        Ce solde est une "avance" du compte Global.

        À la suppression :
        - Le compte Global doit RÉCUPÉRER ce solde → Global.balance -= partner_balance
        - Les comptes agents ne sont PAS touchés (ils ont déjà l'argent en main)
        - Le compte partenaire est supprimé
        - Toutes les transactions liées sont supprimées
        """
        partner_account = Account.objects.filter(
            partner=partner, account_type='partner').first()

        # Récupérer le solde du partenaire AVANT tout
        partner_balance = partner_account.balance if partner_account else Decimal(
            '0.00')

        # Compter et lister les transactions liées
        transaction_count = 0
        total_amount = Decimal('0.00')

        if partner_account:
            transactions_qs = Transaction.objects.filter(
                models.Q(from_account=partner_account) |
                models.Q(to_account=partner_account)
            )
            transaction_count = transactions_qs.count()
            total_amount = transactions_qs.aggregate(
                models.Sum('amount'))['amount__sum'] or Decimal('0.00')

            # ✅ AJUSTEMENT DU COMPTE GLOBAL
            # Le global RÉCUPÈRE le solde du partenaire (donc DIMINUE)
            global_acc = FinanceService.get_global_account()
            global_acc.balance -= partner_balance
            global_acc.save()
            print(
                f"💰 Global ajusté: {global_acc.balance} (delta: -{partner_balance})")

            # ✅ SUPPRIMER LES TRANSACTIONS (nécessaire pour libérer le compte)
            transactions_qs.delete()
            print(f"🗑️ {transaction_count} transaction(s) supprimée(s)")

            # ✅ SUPPRIMER LE COMPTE PARTENAIRE
            partner_account.delete()
            print(f"🗑️ Compte partenaire supprimé")

        # ✅ SUPPRIMER LE PARTENAIRE (soft ou hard)
        if hard:
            partner.hard_delete()
        else:
            partner.delete()

        return {
            'partner_id': partner.id,
            'partner_name': partner.name,
            'transactions_reversed': transaction_count,
            'total_amount_reversed': total_amount,
            'partner_balance': partner_balance,
            'account_deleted': partner_account is not None,
            'hard_deleted': hard,
            'message': (
                f"Partenaire '{partner.name}' supprimé. "
                f"{transaction_count} transaction(s) supprimée(s). "
                f"Solde partenaire ({partner_balance} XOF) déduit du compte global. "
                f"Compte partenaire supprimé."
            )
        }

    # ============================================================
    # MÉTHODES DE CONSULTATION
    # ============================================================

    @staticmethod
    def get_partner_balance(partner):
        account = Account.objects.filter(
            partner=partner, account_type='partner').first()
        return account.balance if account else Decimal('0.00')

    @staticmethod
    def get_agent_balance(agent_user):
        account = Account.objects.filter(
            user=agent_user, account_type='agent').first()
        return account.balance if account else Decimal('0.00')

    @staticmethod
    def get_global_balance():
        account = FinanceService.get_global_account()
        return account.balance

    @staticmethod
    def get_partner_transactions(partner, limit=100):
        partner_account = Account.objects.filter(
            partner=partner, account_type='partner').first()
        if not partner_account:
            return Transaction.objects.none()
        return Transaction.objects.filter(
            models.Q(from_account=partner_account) |
            models.Q(to_account=partner_account)
        ).order_by('-created_at')[:limit]

    @staticmethod
    def get_agent_transactions(agent_user, limit=100):
        agent_account = Account.objects.filter(
            user=agent_user, account_type='agent').first()
        if not agent_account:
            return Transaction.objects.none()
        return Transaction.objects.filter(
            models.Q(from_account=agent_account) |
            models.Q(to_account=agent_account)
        ).order_by('-created_at')[:limit]

    @staticmethod
    def get_withdrawal_recipient(recipient_id):
        try:
            return WithdrawalRecipient.objects.get(id=recipient_id)
        except WithdrawalRecipient.DoesNotExist:
            return None

    @staticmethod
    def create_withdrawal_recipient(data):
        return WithdrawalRecipient.objects.create(
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

    @staticmethod
    def get_withdrawal_stats(partner=None, agent=None, date_from=None, date_to=None):
        withdrawals = Transaction.objects.filter(
            transaction_type='withdrawal', is_reversed=False)

        if partner:
            partner_account = Account.objects.filter(
                partner=partner, account_type='partner').first()
            if partner_account:
                withdrawals = withdrawals.filter(from_account=partner_account)

        if agent:
            agent_account = Account.objects.filter(
                user=agent, account_type='agent').first()
            if agent_account:
                withdrawals = withdrawals.filter(to_account=agent_account)

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
    def get_system_stats():
        total_partners = Partner.objects.filter(is_deleted=False).count()
        total_agents = User.objects.filter(role='agent').count()

        global_account = FinanceService.get_global_account()

        partner_accounts = Account.objects.filter(
            account_type='partner', partner__is_deleted=False)
        agent_accounts = Account.objects.filter(account_type='agent')

        total_partner_balance = sum(
            (acc.balance for acc in partner_accounts), Decimal('0.00'))
        total_agent_balance = sum(
            (acc.balance for acc in agent_accounts), Decimal('0.00'))

        transactions = Transaction.objects.filter(is_reversed=False)
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
                'deposits_total': deposits.aggregate(
                    models.Sum('amount'))['amount__sum'] or Decimal('0.00'),
                'transfers': transfers.count(),
                'transfers_total': transfers.aggregate(
                    models.Sum('amount'))['amount__sum'] or Decimal('0.00'),
                'withdrawals': withdrawals.count(),
                'withdrawals_total': withdrawals.aggregate(
                    models.Sum('amount'))['amount__sum'] or Decimal('0.00'),
            }
        }
