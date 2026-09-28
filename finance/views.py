# finances/views.py
from django.db import models
from django.db.models import Q, Sum, Count
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
from collections import defaultdict
from decimal import Decimal

from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Partner, Account, Transaction, WithdrawalRecipient
from .serializers import (
    PartnerSerializer, AccountSerializer, TransactionSerializer,
    DepositSerializer, TransferToAgentSerializer, WithdrawalSerializer,
    TransferBetweenAgentsSerializer,
    WithdrawalRecipientSerializer, WithdrawalRecipientSimpleSerializer,
    PartnerDeletionSerializer
)
from .services import FinanceService

User = get_user_model()


class PartnerViewSet(viewsets.ModelViewSet):
    serializer_class = PartnerSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        include_deleted = self.request.query_params.get(
            'include_deleted', 'false').lower() == 'true'

        qs = Partner.objects.all().order_by('-created_at')
        if not include_deleted:
            qs = qs.filter(is_deleted=False)

        if user.role in ['admin', 'agent']:
            return qs
        return Partner.objects.none()

    def destroy(self, request, *args, **kwargs):
        """Suppression partenaire + annulation transactions + suppression compte"""
        user = request.user
        if user.role != 'admin':
            return Response(
                {"error": "Seul un administrateur peut supprimer un partenaire."},
                status=status.HTTP_403_FORBIDDEN)

        partner = self.get_object()

        if partner.is_deleted:
            return Response(
                {"error": "Ce partenaire a déjà été supprimé."},
                status=status.HTTP_400_BAD_REQUEST)

        serializer = PartnerDeletionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            result = FinanceService.delete_partner_and_reverse(
                partner,
                deleted_by=user,
                reason=serializer.validated_data.get('reason', ''),
                hard=serializer.validated_data.get('hard_delete', False)
            )
            return Response(result, status=status.HTTP_200_OK)
        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {"error": f"Erreur lors de la suppression: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], url_path='restore')
    def restore_partner(self, request, pk=None):
        """Restaure un partenaire soft-deleted"""
        if request.user.role != 'admin':
            return Response({"error": "Non autorisé"}, status=status.HTTP_403_FORBIDDEN)

        try:
            partner = Partner.objects.get(pk=pk)
        except Partner.DoesNotExist:
            return Response({"error": "Partenaire non trouvé"},
                            status=status.HTTP_404_NOT_FOUND)

        if not partner.is_deleted:
            return Response({"error": "Ce partenaire n'est pas supprimé"},
                            status=status.HTTP_400_BAD_REQUEST)

        partner.is_deleted = False
        partner.deleted_at = None
        partner.save(update_fields=['is_deleted', 'deleted_at'])

        return Response({
            "message": f"Partenaire '{partner.name}' restauré.",
            "partner_id": partner.id,
            "partner_name": partner.name
        })

    @action(detail=True, methods=['get'], url_path='transactions')
    def partner_transactions(self, request, pk=None):
        partner = self.get_object()
        if request.user.role not in ['admin', 'agent']:
            return Response({"error": "Non autorisé"}, status=status.HTTP_403_FORBIDDEN)

        partner_account = Account.objects.filter(
            partner=partner, account_type='partner').first()
        if not partner_account:
            return Response([], status=status.HTTP_200_OK)

        transactions = Transaction.objects.filter(
            Q(from_account=partner_account) | Q(to_account=partner_account)
        ).order_by('-created_at')

        limit = request.query_params.get('limit')
        if limit:
            try:
                transactions = transactions[:int(limit)]
            except ValueError:
                pass

        return Response(TransactionSerializer(transactions, many=True).data)

    @action(detail=True, methods=['get'], url_path='account')
    def partner_account(self, request, pk=None):
        partner = self.get_object()
        if request.user.role not in ['admin', 'agent']:
            return Response({"error": "Non autorisé"}, status=status.HTTP_403_FORBIDDEN)

        partner_account = Account.objects.filter(
            partner=partner, account_type='partner').first()
        if not partner_account:
            return Response({"error": "Ce partenaire n'a pas de compte"},
                            status=status.HTTP_404_NOT_FOUND)

        return Response(AccountSerializer(partner_account).data)


class AccountViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AccountSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        partner_id = self.request.query_params.get('partner_id')
        if partner_id:
            try:
                partner = Partner.objects.get(id=partner_id)
                return Account.objects.filter(partner=partner, account_type='partner')
            except Partner.DoesNotExist:
                return Account.objects.none()

        agent_id = self.request.query_params.get('agent_id')
        if agent_id:
            try:
                user_obj = User.objects.get(id=agent_id, role='agent')
                return Account.objects.filter(user=user_obj, account_type='agent')
            except User.DoesNotExist:
                return Account.objects.none()

        if user.role == 'admin':
            return Account.objects.all()
        elif user.role == 'agent':
            return Account.objects.filter(user=user, account_type='agent')
        return Account.objects.none()

    @action(detail=False, methods=['get'], url_path='global')
    def global_account(self, request):
        if request.user.role != 'admin':
            return Response({"detail": "Non autorisé"}, status=403)
        global_acc = FinanceService.get_global_account()
        return Response(self.get_serializer(global_acc).data)


class TransactionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = Transaction.objects.all().order_by('-created_at')

        # Filtres par compte
        account_id = self.request.query_params.get('account')
        if account_id:
            try:
                account = Account.objects.get(id=account_id)
                if user.role == 'admin':
                    return queryset.filter(Q(from_account=account) | Q(to_account=account))
                elif user.role == 'agent':
                    agent_account = Account.objects.filter(
                        user=user, account_type='agent').first()
                    if agent_account and account.id == agent_account.id:
                        return queryset.filter(
                            Q(from_account=account) | Q(to_account=account))
                return Transaction.objects.none()
            except Account.DoesNotExist:
                return Transaction.objects.none()

        partner_id = self.request.query_params.get('partner')
        if partner_id:
            try:
                partner = Partner.objects.get(id=partner_id)
                partner_account = Account.objects.filter(
                    partner=partner, account_type='partner').first()
                if partner_account and user.role in ['admin', 'agent']:
                    return queryset.filter(
                        Q(from_account=partner_account) |
                        Q(to_account=partner_account))
                return Transaction.objects.none()
            except Partner.DoesNotExist:
                return Transaction.objects.none()

        agent_param = self.request.query_params.get('agent')
        if agent_param:
            if agent_param.lower() == 'me':
                if user.role == 'agent':
                    agent_user = user
                else:
                    return Transaction.objects.none()
            else:
                try:
                    agent_user = User.objects.get(
                        id=int(agent_param), role='agent')
                except (ValueError, User.DoesNotExist):
                    return Transaction.objects.none()

            agent_account = Account.objects.filter(
                user=agent_user, account_type='agent').first()
            if agent_account:
                if user.role == 'admin' or (user.role == 'agent' and user.id == agent_user.id):
                    return queryset.filter(
                        Q(from_account=agent_account) |
                        Q(to_account=agent_account))
            return Transaction.objects.none()

        transaction_type = self.request.query_params.get('transaction_type')
        if transaction_type:
            queryset = queryset.filter(transaction_type=transaction_type)

        recipient_id = self.request.query_params.get('recipient')
        if recipient_id:
            try:
                recipient = WithdrawalRecipient.objects.get(id=recipient_id)
                if user.role == 'admin':
                    return queryset.filter(recipient=recipient)
                return Transaction.objects.none()
            except WithdrawalRecipient.DoesNotExist:
                return Transaction.objects.none()

        # Filtres de date
        date_from = self.request.query_params.get('date_from')
        if date_from:
            queryset = queryset.filter(created_at__gte=date_from)
        date_to = self.request.query_params.get('date_to')
        if date_to:
            queryset = queryset.filter(created_at__lte=date_to)

        # Recherche
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(description__icontains=search) |
                Q(recipient_name__icontains=search) |
                Q(recipient_phone__icontains=search))

        # Filtrer les annulées
        include_reversed = self.request.query_params.get(
            'include_reversed', 'false').lower() == 'true'
        if not include_reversed:
            queryset = queryset.filter(is_reversed=False)

        if user.role == 'admin':
            return queryset
        elif user.role == 'agent':
            agent_acc = Account.objects.filter(
                user=user, account_type='agent').first()
            if agent_acc:
                return queryset.filter(
                    Q(from_account=agent_acc) | Q(to_account=agent_acc))
        return Transaction.objects.none()

    def retrieve(self, request, pk=None):
        try:
            transaction = Transaction.objects.get(pk=pk)
        except Transaction.DoesNotExist:
            return Response({"error": "Transaction non trouvée"},
                            status=status.HTTP_404_NOT_FOUND)

        user = request.user
        if user.role not in ['admin', 'agent']:
            return Response({"error": "Non autorisé"}, status=status.HTTP_403_FORBIDDEN)

        if user.role == 'agent':
            agent_account = Account.objects.filter(
                user=user, account_type='agent').first()
            if agent_account:
                if (transaction.from_account != agent_account and
                        transaction.to_account != agent_account):
                    return Response({"error": "Accès refusé"},
                                    status=status.HTTP_403_FORBIDDEN)
        return Response(self.get_serializer(transaction).data)

    @action(detail=False, methods=['post'])
    def deposit(self, request):
        if request.user.role not in ['admin', 'agent']:
            return Response({"error": "Non autorisé"}, status=403)

        serializer = DepositSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            partner = Partner.objects.get(
                id=serializer.validated_data['partner_id'], is_deleted=False)
            new_balance = FinanceService.deposit_partner(
                partner,
                serializer.validated_data['amount'],
                serializer.validated_data.get('description', ''),
                created_by=request.user)
            return Response({
                "message": "Dépôt effectué",
                "partner_balance": new_balance})
        except Partner.DoesNotExist:
            return Response({"error": "Partenaire non trouvé"}, status=404)
        except ValidationError as e:
            return Response({"error": str(e)}, status=400)
        except Exception as e:
            return Response({"error": str(e)}, status=400)

    @action(detail=False, methods=['post'])
    def transfer_to_agent(self, request):
        if request.user.role != 'admin':
            return Response({"error": "Seul un admin peut faire ce transfert"}, status=403)

        serializer = TransferToAgentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            agent = User.objects.get(
                id=serializer.validated_data['agent_id'], role='agent')
            new_balance = FinanceService.transfer_to_agent(
                agent,
                serializer.validated_data['amount'],
                serializer.validated_data.get('description', ''))
            return Response({
                "message": "Transfert effectué",
                "agent_balance": new_balance})
        except User.DoesNotExist:
            return Response({"error": "Agent non trouvé"}, status=404)
        except ValidationError as e:
            return Response({"error": str(e)}, status=400)
        except Exception as e:
            return Response({"error": str(e)}, status=400)

    @action(detail=False, methods=['post'], url_path='transfer_between_agents')
    def transfer_between_agents(self, request):
        user = request.user
        if user.role != 'agent':
            return Response({"error": "Seul un agent peut faire ce transfert"},
                            status=status.HTTP_403_FORBIDDEN)

        serializer = TransferBetweenAgentsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            destinataire = User.objects.get(
                id=serializer.validated_data['agent_destinataire_id'], role='agent')
            if destinataire.id == user.id:
                return Response({"error": "Transfert à soi-même interdit"},
                                status=status.HTTP_400_BAD_REQUEST)

            new_balance, transaction = FinanceService.transfer_between_agents(
                user,
                destinataire,
                serializer.validated_data['amount'],
                serializer.validated_data.get('description', ''))

            return Response({
                "message": "Transfert effectué",
                "transaction_id": transaction.id,
                "amount": transaction.amount,
                "destinataire_name": destinataire.get_full_name() or destinataire.email,
                "new_balance": new_balance})
        except User.DoesNotExist:
            return Response({"error": "Agent destinataire non trouvé"},
                            status=status.HTTP_404_NOT_FOUND)
        except (Account.DoesNotExist, ValidationError) as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'])
    def withdraw(self, request):
        """Retrait partenaire via agent - L'agent reçoit l'argent"""
        if request.user.role != 'agent':
            return Response({"error": "Seul un agent peut enregistrer un retrait"},
                            status=403)

        serializer = WithdrawalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            partner = Partner.objects.get(
                id=serializer.validated_data['partner_id'], is_deleted=False)

            recipient_data = None
            if serializer.validated_data.get('recipient_id'):
                try:
                    recipient = WithdrawalRecipient.objects.get(
                        id=serializer.validated_data['recipient_id'])
                    recipient_data = {'recipient_id': recipient.id}
                except WithdrawalRecipient.DoesNotExist:
                    return Response({"error": "Bénéficiaire non trouvé"}, status=404)
            elif serializer.validated_data.get('recipient_first_name'):
                recipient_data = {
                    'recipient_first_name': serializer.validated_data.get('recipient_first_name'),
                    'recipient_last_name': serializer.validated_data.get('recipient_last_name'),
                    'recipient_phone': serializer.validated_data.get('recipient_phone'),
                    'recipient_email': serializer.validated_data.get('recipient_email', ''),
                    'recipient_document_type': serializer.validated_data.get(
                        'recipient_document_type', 'cni'),
                    'recipient_document_number': serializer.validated_data.get(
                        'recipient_document_number'),
                    'recipient_address': serializer.validated_data.get('recipient_address', ''),
                }
            else:
                return Response({"error": "Bénéficiaire requis"}, status=400)

            new_balance, transaction = FinanceService.withdraw_partner_via_agent(
                partner,
                request.user,
                serializer.validated_data['amount'],
                serializer.validated_data.get('description', ''),
                recipient_data)

            agent_account = Account.objects.get(
                user=request.user, account_type='agent')

            response_data = {
                "message": "Retrait effectué avec succès",
                "partner_balance": new_balance,
                "agent_balance": agent_account.balance,
                "transaction_id": transaction.id,
                "amount": str(transaction.amount),
                "created_at": transaction.created_at}
            if transaction.recipient:
                response_data["recipient"] = {
                    "id": transaction.recipient.id,
                    "name": transaction.recipient.full_name,
                    "phone": transaction.recipient.phone,
                    "document_number": transaction.recipient.document_number}
            return Response(response_data)
        except Partner.DoesNotExist:
            return Response({"error": "Partenaire non trouvé"}, status=404)
        except ValidationError as e:
            return Response({"error": str(e)}, status=400)
        except Exception as e:
            return Response({"error": str(e)}, status=400)

    @action(detail=True, methods=['post'], url_path='reverse')
    def reverse_transaction(self, request, pk=None):
        """Annule une transaction manuellement"""
        if request.user.role != 'admin':
            return Response({"error": "Seul un admin peut annuler"},
                            status=status.HTTP_403_FORBIDDEN)

        try:
            transaction = Transaction.objects.get(pk=pk)
        except Transaction.DoesNotExist:
            return Response({"error": "Transaction non trouvée"},
                            status=status.HTTP_404_NOT_FOUND)

        if transaction.is_reversed:
            return Response({"error": "Transaction déjà annulée"},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            reversal = FinanceService.reverse_transaction(
                transaction,
                reversed_by=request.user,
                reason=request.data.get('reason', ''))
            return Response({
                "message": "Transaction annulée",
                "original_transaction_id": transaction.id,
                "reversal_transaction_id": reversal.id,
                "amount_reversed": str(transaction.amount)})
        except ValidationError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'], url_path='partner-balance')
    def partner_balance(self, request):
        partner_id = request.query_params.get('partner_id')
        if not partner_id:
            return Response({"error": "partner_id requis"},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            partner = Partner.objects.get(id=partner_id)
        except Partner.DoesNotExist:
            return Response({"error": "Partenaire non trouvé"},
                            status=status.HTTP_404_NOT_FOUND)

        if request.user.role not in ['admin', 'agent']:
            return Response({"error": "Non autorisé"}, status=status.HTTP_403_FORBIDDEN)

        partner_account = Account.objects.filter(
            partner=partner, account_type='partner').first()
        if not partner_account:
            return Response({"error": "Pas de compte"},
                            status=status.HTTP_404_NOT_FOUND)

        return Response({
            "partner_id": partner.id,
            "partner_name": partner.name,
            "balance": partner_account.balance,
            "currency": partner_account.currency,
            "account_id": partner_account.id})

    @action(detail=False, methods=['get'], url_path='agent-balance')
    def agent_balance(self, request):
        if request.user.role != 'agent':
            return Response({"error": "Seul un agent peut voir son solde"},
                            status=status.HTTP_403_FORBIDDEN)

        agent_account = Account.objects.filter(
            user=request.user, account_type='agent').first()
        if not agent_account:
            return Response({"error": "Pas de compte"},
                            status=status.HTTP_404_NOT_FOUND)

        return Response({
            "balance": agent_account.balance,
            "currency": agent_account.currency,
            "account_id": agent_account.id,
            "created_at": agent_account.created_at})

    @action(detail=False, methods=['get'], url_path='recipients')
    def list_recipients(self, request):
        if request.user.role not in ['admin', 'agent']:
            return Response({"error": "Non autorisé"}, status=status.HTTP_403_FORBIDDEN)

        queryset = WithdrawalRecipient.objects.all().order_by('-created_at')
        search = request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(phone__icontains=search) |
                Q(email__icontains=search) |
                Q(document_number__icontains=search))

        limit = request.query_params.get('limit')
        if limit:
            try:
                queryset = queryset[:int(limit)]
            except ValueError:
                pass

        if request.query_params.get('simple') == 'true':
            serializer = WithdrawalRecipientSimpleSerializer(queryset, many=True)
        else:
            serializer = WithdrawalRecipientSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['post'], url_path='recipients/create')
    def create_recipient(self, request):
        if request.user.role not in ['admin', 'agent']:
            return Response({"error": "Non autorisé"}, status=status.HTTP_403_FORBIDDEN)

        serializer = WithdrawalRecipientSerializer(data=request.data)
        if serializer.is_valid():
            recipient = serializer.save()
            return Response(
                WithdrawalRecipientSerializer(recipient).data,
                status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'], url_path='withdrawal-stats')
    def withdrawal_stats(self, request):
        if request.user.role not in ['admin', 'agent']:
            return Response({"error": "Non autorisé"}, status=status.HTTP_403_FORBIDDEN)

        withdrawals = Transaction.objects.filter(
            transaction_type='withdrawal', is_reversed=False)
        total_count = withdrawals.count()
        total_amount = withdrawals.aggregate(Sum('amount'))['amount__sum'] or 0

        top_recipients = (
            WithdrawalRecipient.objects
            .annotate(total_withdrawals=Count('transactions'))
            .annotate(total_amount=Sum('transactions__amount'))
            .filter(total_withdrawals__gt=0)
            .order_by('-total_withdrawals')[:10])

        by_month = defaultdict(lambda: {'count': 0, 'total': 0})
        for w in withdrawals:
            month_key = w.created_at.strftime('%Y-%m')
            by_month[month_key]['count'] += 1
            by_month[month_key]['total'] += float(w.amount)

        by_month_list = [
            {'month': k, 'count': v['count'], 'total': v['total']}
            for k, v in sorted(by_month.items())]

        return Response({
            "total_withdrawals": total_count,
            "total_amount": total_amount,
            "average_amount": total_amount / total_count if total_count > 0 else 0,
            "top_recipients": [
                {"id": r.id, "name": r.full_name, "phone": r.phone,
                 "total_withdrawals": r.total_withdrawals,
                 "total_amount": r.total_amount}
                for r in top_recipients],
            "by_month": by_month_list})

    @action(detail=False, methods=['get'], url_path='global-stats')
    def global_stats(self, request):
        if request.user.role not in ['admin', 'agent']:
            return Response({"error": "Non autorisé"}, status=status.HTTP_403_FORBIDDEN)

        transactions = Transaction.objects.filter(is_reversed=False)
        type_stats = (
            transactions.values('transaction_type')
            .annotate(count=Count('id'), total=Sum('amount'))
            .order_by('transaction_type'))

        today = timezone.now().date()
        today_transactions = transactions.filter(created_at__date=today)
        week_ago = timezone.now() - timedelta(days=7)
        week_transactions = transactions.filter(created_at__gte=week_ago)

        return Response({
            "by_type": [
                {"type": item['transaction_type'],
                 "label": dict(Transaction.TRANSACTION_TYPES).get(
                     item['transaction_type'], item['transaction_type']),
                 "count": item['count'], "total": item['total']}
                for item in type_stats],
            "today": {
                "count": today_transactions.count(),
                "total": today_transactions.aggregate(
                    Sum('amount'))['amount__sum'] or 0},
            "this_week": {
                "count": week_transactions.count(),
                "total": week_transactions.aggregate(
                    Sum('amount'))['amount__sum'] or 0}})

    @action(detail=False, methods=['get'], url_path='export')
    def export_transactions(self, request):
        if request.user.role != 'admin':
            return Response({"error": "Seul un admin peut exporter"},
                            status=status.HTTP_403_FORBIDDEN)

        queryset = self.get_queryset()
        limit = request.query_params.get('limit', 1000)
        try:
            queryset = queryset[:int(limit)]
        except ValueError:
            queryset = queryset[:1000]

        import csv
        from django.http import HttpResponse
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="transactions.csv"'
        writer = csv.writer(response)
        writer.writerow(['ID', 'Type', 'Montant', 'De', 'Vers', 'Description',
                         'Bénéficiaire', 'Téléphone', 'Créé par', 'Date', 'Annulée'])
        for t in queryset:
            writer.writerow([
                t.id, t.get_transaction_type_display(), str(t.amount),
                str(t.from_account) if t.from_account else t.from_account_label_snapshot,
                str(t.to_account) if t.to_account else t.to_account_label_snapshot,
                t.description, t.recipient_name or '', t.recipient_phone or '',
                t.created_by.email if t.created_by else 'Système',
                t.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                'Oui' if t.is_reversed else 'Non'])
        return response

    @action(detail=False, methods=['get'], url_path='by-partner')
    def by_partner(self, request):
        partner_id = request.query_params.get('partner_id')
        if not partner_id:
            return Response({"error": "partner_id requis"},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            partner = Partner.objects.get(id=partner_id)
        except Partner.DoesNotExist:
            return Response({"error": "Partenaire non trouvé"},
                            status=status.HTTP_404_NOT_FOUND)

        if request.user.role not in ['admin', 'agent']:
            return Response({"error": "Non autorisé"}, status=status.HTTP_403_FORBIDDEN)

        partner_account = Account.objects.filter(
            partner=partner, account_type='partner').first()
        if not partner_account:
            return Response({"error": "Pas de compte"},
                            status=status.HTTP_404_NOT_FOUND)

        transactions = Transaction.objects.filter(
            Q(from_account=partner_account) | Q(to_account=partner_account)
        ).order_by('-created_at')

        limit = request.query_params.get('limit', 50)
        try:
            transactions = transactions[:int(limit)]
        except ValueError:
            transactions = transactions[:50]

        return Response({
            "partner": {"id": partner.id, "name": partner.name,
                        "balance": partner_account.balance},
            "transactions": TransactionSerializer(transactions, many=True).data,
            "count": transactions.count()})


class AgentBalanceViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        user = request.user
        if not user.is_authenticated:
            return Response({"error": "Auth requise"},
                            status=status.HTTP_401_UNAUTHORIZED)

        agents = User.objects.filter(role='agent')
        result = []
        for agent in agents:
            if user.role == 'agent' and agent.id == user.id:
                continue
            account = Account.objects.filter(
                user=agent, account_type='agent').first()
            result.append({
                "id": agent.id,
                "email": agent.email,
                "full_name": agent.get_full_name(),
                "username": agent.username,
                "phone_number": getattr(agent, 'phone_number', ''),
                "balance": account.balance if account else 0,
                "account_id": account.id if account else None,
                "currency": account.currency if account else 'XOF',
                "is_active": agent.is_active,
                "is_online": getattr(agent, 'is_online', False),
                "created_at": getattr(agent, 'created_at', None),
                "last_login": agent.last_login})
        return Response(result)

    @action(detail=False, methods=['get'], url_path='me')
    def my_balance(self, request):
        user = request.user
        if user.role != 'agent':
            return Response({"error": "Seul un agent"},
                            status=status.HTTP_403_FORBIDDEN)

        account = Account.objects.filter(
            user=user, account_type='agent').first()
        if not account:
            return Response({"error": "Pas de compte"},
                            status=status.HTTP_404_NOT_FOUND)

        return Response({
            "id": user.id, "email": user.email,
            "full_name": user.get_full_name(),
            "balance": account.balance,
            "currency": account.currency,
            "account_id": account.id,
            "created_at": account.created_at})

    @action(detail=True, methods=['get'], url_path='balance')
    def agent_balance_detail(self, request, pk=None):
        user = request.user
        if user.role not in ['admin', 'agent']:
            return Response({"error": "Non autorisé"}, status=status.HTTP_403_FORBIDDEN)

        try:
            agent = User.objects.get(pk=pk, role='agent')
        except User.DoesNotExist:
            return Response({"error": "Agent non trouvé"},
                            status=status.HTTP_404_NOT_FOUND)

        if user.role == 'agent' and agent.id != user.id:
            return Response({"error": "Accès refusé"}, status=status.HTTP_403_FORBIDDEN)

        account = Account.objects.filter(
            user=agent, account_type='agent').first()
        return Response({
            "id": agent.id, "email": agent.email,
            "full_name": agent.get_full_name(),
            "balance": account.balance if account else 0,
            "account_id": account.id if account else None,
            "currency": account.currency if account else 'XOF'})


class WithdrawalRecipientViewSet(viewsets.ModelViewSet):
    serializer_class = WithdrawalRecipientSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = WithdrawalRecipient.objects.all().order_by('-created_at')

    def get_queryset(self):
        queryset = super().get_queryset()
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(phone__icontains=search) |
                Q(document_number__icontains=search))
        return queryset

    def get_permissions(self):
        if self.request.user.role not in ['admin', 'agent']:
            self.permission_classes = [permissions.IsAuthenticated]
        return super().get_permissions()


class StatisticsViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        if request.user.role not in ['admin', 'agent']:
            return Response({"error": "Non autorisé"}, status=status.HTTP_403_FORBIDDEN)

        return Response(FinanceService.get_system_stats())