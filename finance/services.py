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
    # HELPERS INTERNES — Toute écriture de solde passe par ici
    # ============================================================

    @staticmethod
    def _check_positive(amount):
        """Valide qu'un montant est strictement positif."""
        amount = Decimal(str(amount))
        if amount <= 0:
            raise ValidationError("Le montant doit être positif.")
        return amount

    @staticmethod
    def _credit(account, amount):
        """Crédite un compte (aucun risque de négatif)."""
        account.balance += amount
        account.save(update_fields=['balance', 'updated_at'])

    @staticmethod
    def _debit(account, amount):
        """
        Débite un compte en REFUSANT tout solde négatif.
        Utilisé pour :
          - Global (toujours)
          - Partner et Agent pour les débits qui NE sont PAS un retrait
            (ex : transfert agent→agent, annulation de transfert)
        """
        if account.balance < amount:
            raise ValidationError(
                f"Solde insuffisant sur '{account}'. "
                f"Solde actuel: {account.balance} {account.currency}, "
                f"demandé: {amount} {account.currency}")
        account.balance -= amount
        account.save(update_fields=['balance', 'updated_at'])

    @staticmethod
    def _debit_allow_negative(account, amount):
        """
        Débite un compte SANS vérifier le solde.
        ⚠️ Réservé EXCLUSIVEMENT aux comptes partner et agent,
        dans le cadre d'un retrait partenaire (le partenaire peut retirer
        plus que son solde, l'agent avance le cash au bénéficiaire).
        Le Global ne peut JAMAIS être négatif, même par cette voie.
        """
        if account.account_type not in ('partner', 'agent'):
            raise ValidationError(
                "Le découvert n'est autorisé que pour les comptes "
                "partenaire et agent.")
        account.balance -= amount
        account.save(update_fields=['balance', 'updated_at'])

    @staticmethod
    def _debit_or_zero(account, amount):
        """
        Débite un compte sans jamais le rendre négatif.
        Si le solde est insuffisant, met le solde à zéro et retourne
        le montant effectivement débité (peut être < amount).
        Utilisé uniquement pour des cas exceptionnels (ex : suppression
        d'un partenaire quand le Global n'a pas assez pour rembourser).
        """
        debited = min(account.balance, amount)
        account.balance -= debited
        account.save(update_fields=['balance', 'updated_at'])
        return debited

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
    # APPROVISIONNEMENT DU GLOBAL
    # ============================================================

    @staticmethod
    @db_transaction.atomic
    def topup_global(amount, description="", created_by=None):
        """
        Approvisionne le compte Global (trésor de l'entreprise).
        Entrée externe : le Global est crédité, aucun débit interne.
        """
        amount = FinanceService._check_positive(amount)
        global_acc = FinanceService.get_global_account()
        FinanceService._credit(global_acc, amount)

        Transaction.objects.create(
            transaction_type='company_topup',
            from_account=None,
            to_account=global_acc,
            amount=amount,
            description=description or "Approvisionnement compte global",
            created_by=created_by,
        )
        return global_acc.balance

    # ============================================================
    # OPÉRATIONS FINANCIÈRES
    # ============================================================

    @staticmethod
    @db_transaction.atomic
    def deposit_partner(partner, amount, description="", created_by=None):
        """
        DÉPÔT Partenaire (ENTRÉE) :
        Le partenaire apporte du cash au système.
        - Partenaire : +amount (peut sortir du découvert)
        - Global     : +amount

        AUCUN DÉBIT → ni le Global ni le Partenaire ne peuvent
        devenir négatifs ici.
        """
        amount = FinanceService._check_positive(amount)

        if partner.is_deleted:
            raise ValidationError("Ce partenaire est supprimé.")

        partner_acc = FinanceService.get_or_create_partner_account(partner)
        global_acc = FinanceService.get_global_account()

        # ✅ Les DEUX comptes CRÉDITENT (entrée externe d'argent)
        FinanceService._credit(partner_acc, amount)
        FinanceService._credit(global_acc, amount)

        Transaction.objects.create(
            transaction_type='deposit',
            from_account=None,          # entrée externe : aucun compte source
            to_account=partner_acc,
            amount=amount,
            description=description,
            created_by=created_by,
        )
        return partner_acc.balance

    @staticmethod
    @db_transaction.atomic
    def transfer_to_agent(agent_user, amount, description="", created_by=None):
        """
        Transfert Global → Agent :
        - Global : −amount (vérifié — ne peut pas passer négatif)
        - Agent  : +amount (crédit, peut sortir du découvert)
        """
        amount = FinanceService._check_positive(amount)

        global_acc = FinanceService.get_global_account()
        agent_acc = FinanceService.get_or_create_agent_account(agent_user)

        FinanceService._debit(global_acc, amount)
        FinanceService._credit(agent_acc, amount)

        Transaction.objects.create(
            transaction_type='transfer_to_agent',
            from_account=global_acc,
            to_account=agent_acc,
            amount=amount,
            description=description,
            created_by=created_by,
        )
        return agent_acc.balance

    @staticmethod
    @db_transaction.atomic
    def transfer_between_agents(from_agent, to_agent, amount, description=""):
        """
        Transfert Agent → Agent :
        - Source      : −amount (vérifié — pas de découvert ici)
        - Destination : +amount
        """
        amount = FinanceService._check_positive(amount)

        if from_agent.id == to_agent.id:
            raise ValidationError(
                "Vous ne pouvez pas vous transférer de l'argent à vous-même.")

        from_acc = FinanceService.get_or_create_agent_account(from_agent)
        to_acc = FinanceService.get_or_create_agent_account(to_agent)

        # ✅ Ici on REFUSE le découvert : un agent ne peut pas transférer
        #    plus qu'il ne possède.
        FinanceService._debit(from_acc, amount)
        FinanceService._credit(to_acc, amount)

        transaction = Transaction.objects.create(
            transaction_type='transfer_between_agents',
            from_account=from_acc,
            to_account=to_acc,
            amount=amount,
            description=description,
            created_by=from_agent,
        )
        return from_acc.balance, transaction

    @staticmethod
    @db_transaction.atomic
    def withdraw_partner_via_agent(partner, agent_user, amount,
                                   description="", recipient_data=None):
        """
        RETRAIT Partenaire (SORTIE) :
        - Partenaire : −amount (DÉCOUVERT AUTORISÉ — le partenaire peut
                       retirer plus que son solde, son compte passe
                       en négatif ; il comblera par un futur dépôt)
        - Agent      : −amount (DÉCOUVERT AUTORISÉ — l'agent avance le
                       cash au bénéficiaire même s'il n'a pas assez
                       en caisse)

        AUCUNE vérification de solde sur ces deux comptes.
        Le Global n'est PAS touché.
        L'argent sort du système (le bénéficiaire repart avec le cash).
        """
        amount = FinanceService._check_positive(amount)

        if partner.is_deleted:
            raise ValidationError("Ce partenaire est supprimé.")

        partner_acc = FinanceService.get_or_create_partner_account(partner)
        agent_acc = FinanceService.get_or_create_agent_account(agent_user)

        # ✅ Partenaire : découvert AUTORISÉ
        FinanceService._debit_allow_negative(partner_acc, amount)

        # ✅ Agent : découvert AUTORISÉ
        FinanceService._debit_allow_negative(agent_acc, amount)

        # ── Gestion du bénéficiaire ──
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
        Annule une transaction en appliquant l'INVERSE EXACT de l'opération
        d'origine.

        - deposit               : Partner +, Global +     → Partner −, Global − (vérifiés)
        - transfer_to_agent     : Global −, Agent +       → Global +, Agent − (vérifiés)
        - withdrawal            : Partner −, Agent −      → Partner +, Agent + (crédits)
        - transfer_between_agents : Source −, Dest +      → Source +, Dest − (vérifiés)
        - company_topup         : Global +                → Global − (vérifié)

        ⚠️ L'annulation d'un dépôt DÉBITE le Global et le Partenaire.
        Si l'un des deux n'a pas les fonds, l'annulation est refusée.
        """
        if transaction.is_reversed:
            raise ValidationError("Cette transaction a déjà été annulée.")

        if transaction.transaction_type in Transaction.NON_REVERSIBLE_TYPES:
            raise ValidationError(
                f"Une transaction de type "
                f"'{transaction.get_transaction_type_display()}' "
                f"ne peut pas être annulée."
            )

        from_account = transaction.from_account
        to_account = transaction.to_account
        amount = transaction.amount

        if from_account is None and to_account is None:
            raise ValidationError(
                "Impossible d'annuler : les comptes liés ont été supprimés.")

        tx_type = transaction.transaction_type

        # ── Inversion selon le type d'origine ──
        if tx_type == 'deposit':
            # Dépôt : Partner +, Global + → inverse : Partner −, Global −
            global_acc = FinanceService.get_global_account()
            FinanceService._debit(global_acc, amount)   # vérifié
            if to_account:
                FinanceService._debit(to_account, amount)  # vérifié

        elif tx_type == 'transfer_to_agent':
            # Global −, Agent + → inverse : Global +, Agent −
            if from_account:
                FinanceService._credit(from_account, amount)
            if to_account:
                # On REFUSE le découvert ici : annuler un transfert ne doit
                # pas plonger l'agent dans le rouge.
                FinanceService._debit(to_account, amount)

        elif tx_type == 'withdrawal':
            # Partner −, Agent − → inverse : Partner +, Agent +
            if from_account:
                FinanceService._credit(from_account, amount)
            if to_account:
                FinanceService._credit(to_account, amount)

        elif tx_type == 'transfer_between_agents':
            # Source −, Dest + → inverse : Source +, Dest −
            if from_account:
                FinanceService._credit(from_account, amount)
            if to_account:
                FinanceService._debit(to_account, amount)

        elif tx_type == 'company_topup':
            # Global + → inverse : Global − (vérifié)
            if to_account:
                FinanceService._debit(to_account, amount)

        else:
            # Fallback générique (ne devrait pas arriver)
            if from_account:
                FinanceService._credit(from_account, amount)
            if to_account:
                FinanceService._debit(to_account, amount)

        # ── Création de la transaction d'annulation ──
        reversal = Transaction.objects.create(
            transaction_type='reversal',
            from_account=to_account,
            to_account=from_account,
            amount=amount,
            description=(
                f"Annulation de la transaction #{transaction.id} "
                f"({transaction.get_transaction_type_display()})"
                + (f" — Motif : {reason}" if reason else "")
            ),
            created_by=reversed_by,
        )

        # ── Marquage de la transaction originale ──
        transaction.is_reversed = True
        transaction.reversed_at = timezone.now()
        transaction.reversal_transaction = reversal
        transaction.save(update_fields=[
            'is_reversed', 'reversed_at', 'reversal_transaction'])

        return reversal

    # ============================================================
    # SUPPRESSION PARTENAIRE
    # ============================================================

    @staticmethod
    @db_transaction.atomic
    def delete_partner_and_reverse(partner, deleted_by=None, reason="",
                                   hard=False):
        """
        Supprime un partenaire et RESTAURE les soldes correctement.

        Cas 1 — Partenaire avec solde POSITIF :
            Le solde est remboursé au Global (débit Global, vérifié).
        Cas 2 — Partenaire avec solde NÉGATIF (découvert) :
            Le découvert est ANNULÉ en créditant le Global du montant
            (le Global reprend ce qu'il avait "avancé" en trop).

        Puis :
            - Les transactions liées sont supprimées.
            - Le compte partenaire est supprimé.
            - Le partenaire est soft-deleted (ou hard-deleted).
        """
        partner_account = Account.objects.filter(
            partner=partner, account_type='partner').first()

        partner_balance = (
            partner_account.balance if partner_account else Decimal('0.00'))

        transaction_count = 0
        total_amount = Decimal('0.00')
        refunded = Decimal('0.00')
        shortfall = Decimal('0.00')
        overdraft_cleared = Decimal('0.00')

        if partner_account:
            transactions_qs = Transaction.objects.filter(
                models.Q(from_account=partner_account) |
                models.Q(to_account=partner_account)
            )
            transaction_count = transactions_qs.count()
            total_amount = transactions_qs.aggregate(
                models.Sum('amount'))['amount__sum'] or Decimal('0.00')

            global_acc = FinanceService.get_global_account()

            if partner_balance > 0:
                # ✅ Remboursement du solde partenaire au Global
                refunded = FinanceService._debit_or_zero(
                    global_acc, partner_balance)
                shortfall = partner_balance - refunded

            elif partner_balance < 0:
                # ✅ Découvert partenaire → on crédite le Global du montant
                #    (le Global récupère l'avance qu'il avait faite en trop)
                overdraft_cleared = -partner_balance
                FinanceService._credit(global_acc, overdraft_cleared)

            transactions_qs.delete()
            partner_account.delete()

        if hard:
            partner.hard_delete()
        else:
            partner.delete()

        message = (
            f"Partenaire '{partner.name}' supprimé. "
            f"{transaction_count} transaction(s) supprimée(s). "
            f"Solde partenaire au moment de la suppression : "
            f"{partner_balance} XOF."
        )
        if refunded > 0:
            message += f" Remboursé au Global : {refunded} XOF."
        if shortfall > 0:
            message += (
                f" ⚠️ Manque {shortfall} XOF (Global insuffisant, "
                f"traité comme perte exceptionnelle).")
        if overdraft_cleared > 0:
            message += (
                f" Découvert de {overdraft_cleared} XOF annulé "
                f"(crédité au Global).")

        return {
            'partner_id': partner.id,
            'partner_name': partner.name,
            'transactions_reversed': transaction_count,
            'total_amount_reversed': total_amount,
            'partner_balance': partner_balance,
            'refunded_to_global': refunded,
            'shortfall': shortfall,
            'overdraft_cleared': overdraft_cleared,
            'account_deleted': partner_account is not None,
            'hard_deleted': hard,
            'message': message,
        }

    # ============================================================
    # CONTRÔLE D'INTÉGRITÉ
    # ============================================================

    @staticmethod
    def verify_integrity():
        """
        Vérifie la cohérence des soldes.

        - Global   : ne doit JAMAIS être négatif.
        - Partner  : peut être négatif (découvert autorisé en retrait).
        - Agent    : peut être négatif (découvert autorisé en retrait).
        """
        accounts = Account.objects.all()

        forbidden_negatives = [
            {
                'id': a.id,
                'label': str(a),
                'account_type': a.account_type,
                'balance': str(a.balance),
            }
            for a in accounts
            if a.balance < 0 and a.account_type == 'global'
        ]

        partners_in_overdraft = [
            {
                'id': a.id,
                'label': str(a),
                'balance': str(a.balance),
                'overdraft': str(-a.balance),
            }
            for a in accounts.filter(account_type='partner', balance__lt=0)
        ]

        agents_in_overdraft = [
            {
                'id': a.id,
                'label': str(a),
                'balance': str(a.balance),
                'overdraft': str(-a.balance),
            }
            for a in accounts.filter(account_type='agent', balance__lt=0)
        ]

        partner_overdraft_total = sum(
            (-a.balance for a in accounts.filter(
                account_type='partner', balance__lt=0)),
            Decimal('0.00'))
        agent_overdraft_total = sum(
            (-a.balance for a in accounts.filter(
                account_type='agent', balance__lt=0)),
            Decimal('0.00'))

        global_bal = FinanceService.get_global_balance()
        partner_total = sum(
            (a.balance for a in accounts.filter(account_type='partner')),
            Decimal('0.00'))
        agent_total = sum(
            (a.balance for a in accounts.filter(account_type='agent')),
            Decimal('0.00'))

        return {
            'has_forbidden_negatives': len(forbidden_negatives) > 0,
            'forbidden_negative_accounts': forbidden_negatives,
            'partners_in_overdraft': partners_in_overdraft,
            'partner_overdraft_total': str(partner_overdraft_total),
            'agents_in_overdraft': agents_in_overdraft,
            'agent_overdraft_total': str(agent_overdraft_total),
            'global': str(global_bal),
            'partners_total': str(partner_total),
            'agents_total': str(agent_total),
            'internal_total': str(global_bal + partner_total + agent_total),
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
    def get_withdrawal_stats(partner=None, agent=None,
                             date_from=None, date_to=None):
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
            'average_amount': (
                total_amount / total_count
                if total_count > 0 else Decimal('0.00'))
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

        partners_in_overdraft_qs = partner_accounts.filter(balance__lt=0)
        agents_in_overdraft_qs = agent_accounts.filter(balance__lt=0)

        partner_overdraft_total = sum(
            (-acc.balance for acc in partners_in_overdraft_qs),
            Decimal('0.00'))
        agent_overdraft_total = sum(
            (-acc.balance for acc in agents_in_overdraft_qs),
            Decimal('0.00'))

        transactions = Transaction.objects.filter(is_reversed=False)
        total_transactions = transactions.count()

        deposits = transactions.filter(transaction_type='deposit')
        transfers = transactions.filter(transaction_type='transfer_to_agent')
        withdrawals = transactions.filter(transaction_type='withdrawal')
        topups = transactions.filter(transaction_type='company_topup')

        return {
            'partners': {
                'total': total_partners,
                'total_balance': total_partner_balance,
                'active': partner_accounts.filter(balance__gt=0).count(),
                'in_overdraft': partners_in_overdraft_qs.count(),
                'overdraft_total': partner_overdraft_total,
            },
            'agents': {
                'total': total_agents,
                'total_balance': total_agent_balance,
                'active': agent_accounts.filter(balance__gt=0).count(),
                'in_overdraft': agents_in_overdraft_qs.count(),
                'overdraft_total': agent_overdraft_total,
            },
            'global_account': {
                'balance': global_account.balance,
                'currency': global_account.currency,
                'is_negative': global_account.balance < 0,
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
                'topups': topups.count(),
                'topups_total': topups.aggregate(
                    models.Sum('amount'))['amount__sum'] or Decimal('0.00'),
            }
        }
