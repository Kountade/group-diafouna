# finance/views.py
from django.shortcuts import render
from django.db import models
from django.db.models import Q, Sum, Count
from django.db.models.functions import TruncMonth, TruncDate
from django.core.exceptions import ValidationError
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta

from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Partner, Account, Transaction, WithdrawalRecipient
from .serializers import (
    PartnerSerializer, AccountSerializer, TransactionSerializer,
    DepositSerializer, TransferToAgentSerializer, WithdrawalSerializer,
    TransferBetweenAgentsSerializer,  # ← AJOUT IMPORTANT
    WithdrawalRecipientSerializer, WithdrawalRecipientSimpleSerializer
)
from .services import FinanceService

User = get_user_model()


class PartnerViewSet(viewsets.ModelViewSet):
    """CRUD pour les partenaires (admin uniquement)"""
    queryset = Partner.objects.all()
    serializer_class = PartnerSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_permissions(self):
        if self.request.user.role != 'admin':
            self.permission_classes = [permissions.IsAuthenticated]
        return super().get_permissions()

    @action(detail=True, methods=['get'], url_path='transactions')
    def partner_transactions(self, request, pk=None):
        """
        Récupère toutes les transactions d'un partenaire spécifique
        Usage: GET /api/partners/{id}/transactions/?limit=5
        """
        partner = self.get_object()
        user = request.user

        if user.role not in ['admin', 'agent']:
            return Response(
                {"error": "Vous n'avez pas les droits pour voir ces transactions"},
                status=status.HTTP_403_FORBIDDEN
            )

        partner_account = Account.objects.filter(
            partner=partner,
            account_type='partner'
        ).first()

        if not partner_account:
            return Response([], status=status.HTTP_200_OK)

        transactions = Transaction.objects.filter(
            Q(from_account=partner_account) |
            Q(to_account=partner_account)
        ).order_by('-created_at')

        limit = request.query_params.get('limit')
        if limit:
            try:
                transactions = transactions[:int(limit)]
            except ValueError:
                pass

        serializer = TransactionSerializer(transactions, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'], url_path='account')
    def partner_account(self, request, pk=None):
        """
        Récupère le compte d'un partenaire spécifique
        Usage: GET /api/partners/{id}/account/
        """
        partner = self.get_object()
        user = request.user

        if user.role not in ['admin', 'agent']:
            return Response(
                {"error": "Vous n'avez pas les droits pour voir ce compte"},
                status=status.HTTP_403_FORBIDDEN
            )

        partner_account = Account.objects.filter(
            partner=partner,
            account_type='partner'
        ).first()

        if not partner_account:
            return Response(
                {"error": "Ce partenaire n'a pas encore de compte"},
                status=status.HTTP_404_NOT_FOUND
            )

        serializer = AccountSerializer(partner_account)
        return Response(serializer.data)


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
        serializer = self.get_serializer(global_acc)
        return Response(serializer.data)


class TransactionViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = TransactionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = Transaction.objects.all().order_by('-created_at')

        # Filtrage par compte spécifique
        account_id = self.request.query_params.get('account')
        if account_id:
            try:
                account = Account.objects.get(id=account_id)
                if user.role == 'admin':
                    return queryset.filter(
                        Q(from_account=account) | Q(to_account=account)
                    )
                elif user.role == 'agent':
                    agent_account = Account.objects.filter(
                        user=user, account_type='agent').first()
                    if agent_account and account.id == agent_account.id:
                        return queryset.filter(
                            Q(from_account=account) | Q(to_account=account)
                        )
                    return Transaction.objects.none()
                return Transaction.objects.none()
            except Account.DoesNotExist:
                return Transaction.objects.none()

        # Filtrage par partenaire
        partner_id = self.request.query_params.get('partner')
        if partner_id:
            try:
                partner = Partner.objects.get(id=partner_id)
                partner_account = Account.objects.filter(
                    partner=partner, account_type='partner').first()
                if partner_account:
                    if user.role in ['admin', 'agent']:
                        return queryset.filter(
                            Q(from_account=partner_account) |
                            Q(to_account=partner_account)
                        )
                return Transaction.objects.none()
            except Partner.DoesNotExist:
                return Transaction.objects.none()

        # Filtrage par agent
        agent_id = self.request.query_params.get('agent')
        if agent_id:
            try:
                agent_user = User.objects.get(id=agent_id, role='agent')
                agent_account = Account.objects.filter(
                    user=agent_user, account_type='agent').first()
                if agent_account:
                    if user.role == 'admin':
                        return queryset.filter(
                            Q(from_account=agent_account) |
                            Q(to_account=agent_account)
                        )
                return Transaction.objects.none()
            except User.DoesNotExist:
                return Transaction.objects.none()

        # Filtrage par type de transaction
        transaction_type = self.request.query_params.get('transaction_type')
        if transaction_type:
            return queryset.filter(transaction_type=transaction_type)

        # Filtrage par bénéficiaire
        recipient_id = self.request.query_params.get('recipient')
        if recipient_id:
            try:
                recipient = WithdrawalRecipient.objects.get(id=recipient_id)
                if user.role == 'admin':
                    return queryset.filter(recipient=recipient)
                return Transaction.objects.none()
            except WithdrawalRecipient.DoesNotExist:
                return Transaction.objects.none()

        # Filtrage par date (depuis/vers)
        date_from = self.request.query_params.get('date_from')
        if date_from:
            queryset = queryset.filter(created_at__gte=date_from)

        date_to = self.request.query_params.get('date_to')
        if date_to:
            queryset = queryset.filter(created_at__lte=date_to)

        # Filtrage par mot-clé dans la description
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(description__icontains=search) |
                Q(recipient_name__icontains=search) |
                Q(recipient_phone__icontains=search)
            )

        # Filtrage par rôle utilisateur
        if user.role == 'admin':
            return queryset
        elif user.role == 'agent':
            agent_acc = Account.objects.filter(
                user=user, account_type='agent').first()
            if agent_acc:
                return queryset.filter(
                    Q(from_account=agent_acc) | Q(to_account=agent_acc)
                )
        return Transaction.objects.none()

    # ============================================================
    # 1. DETAIL D'UNE TRANSACTION
    # ============================================================
    def retrieve(self, request, pk=None):
        """Récupère le détail d'une transaction spécifique"""
        try:
            transaction = Transaction.objects.get(pk=pk)
        except Transaction.DoesNotExist:
            return Response(
                {"error": "Transaction non trouvée"},
                status=status.HTTP_404_NOT_FOUND
            )

        user = request.user
        if user.role not in ['admin', 'agent']:
            return Response(
                {"error": "Non autorisé"},
                status=status.HTTP_403_FORBIDDEN
            )

        if user.role == 'agent':
            agent_account = Account.objects.filter(
                user=user, account_type='agent').first()
            if agent_account:
                if (transaction.from_account != agent_account and
                        transaction.to_account != agent_account):
                    return Response(
                        {"error": "Vous n'avez pas accès à cette transaction"},
                        status=status.HTTP_403_FORBIDDEN
                    )
            else:
                return Response(
                    {"error": "Vous n'avez pas de compte associé"},
                    status=status.HTTP_403_FORBIDDEN
                )

        serializer = self.get_serializer(transaction)
        return Response(serializer.data)

    # ============================================================
    # 2. DEPOT PARTENAIRE
    # ============================================================
    @action(detail=False, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def deposit(self, request):
        """Dépôt partenaire (admin ou agent)"""
        if request.user.role not in ['admin', 'agent']:
            return Response({"error": "Non autorisé"}, status=403)

        serializer = DepositSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            partner = Partner.objects.get(
                id=serializer.validated_data['partner_id'])
            new_balance = FinanceService.deposit_partner(
                partner,
                serializer.validated_data['amount'],
                serializer.validated_data.get('description', '')
            )
            return Response({
                "message": "Dépôt effectué",
                "partner_balance": new_balance
            })
        except Partner.DoesNotExist:
            return Response({"error": "Partenaire non trouvé"}, status=404)
        except ValidationError as e:
            return Response({"error": str(e)}, status=400)
        except Exception as e:
            return Response({"error": str(e)}, status=400)

    # ============================================================
    # 3. TRANSFERT GLOBAL → AGENT
    # ============================================================
    @action(detail=False, methods=['post'])
    def transfer_to_agent(self, request):
        """Admin transfère du global à un agent"""
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
                serializer.validated_data.get('description', '')
            )
            return Response({
                "message": "Transfert effectué",
                "agent_balance": new_balance
            })
        except User.DoesNotExist:
            return Response({"error": "Agent non trouvé"}, status=404)
        except ValidationError as e:
            return Response({"error": str(e)}, status=400)
        except Exception as e:
            return Response({"error": str(e)}, status=400)

    # ============================================================
    # 4. TRANSFERT ENTRE AGENTS
    # ============================================================
    @action(detail=False, methods=['post'], url_path='transfer_between_agents')
    def transfer_between_agents(self, request):
        """
        Transfert d'argent entre deux agents
        POST /api/transactions/transfer_between_agents/

        Corps de la requête:
        {
            "agent_destinataire_id": 2,
            "amount": 10000,
            "description": "Remboursement"
        }
        """
        user = request.user

        if user.role != 'agent':
            return Response(
                {"error": "Seul un agent peut effectuer un transfert entre agents"},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = TransferBetweenAgentsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            destinataire = User.objects.get(
                id=serializer.validated_data['agent_destinataire_id'],
                role='agent'
            )

            if destinataire.id == user.id:
                return Response(
                    {"error": "Vous ne pouvez pas vous transférer de l'argent à vous-même"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            from_account = Account.objects.get(user=user, account_type='agent')
            to_account = Account.objects.get(
                user=destinataire, account_type='agent')

            amount = serializer.validated_data['amount']

            if from_account.balance < amount:
                return Response(
                    {"error": f"Solde insuffisant. Solde actuel: {from_account.balance} XOF"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Effectuer le transfert
            from_account.balance -= amount
            to_account.balance += amount
            from_account.save()
            to_account.save()

            transaction = Transaction.objects.create(
                transaction_type='transfer_between_agents',
                from_account=from_account,
                to_account=to_account,
                amount=amount,
                description=serializer.validated_data.get('description', ''),
                created_by=user,
            )

            return Response({
                "message": "Transfert effectué avec succès",
                "transaction_id": transaction.id,
                "amount": transaction.amount,
                "destinataire_name": destinataire.get_full_name() or destinataire.email,
                "new_balance": from_account.balance
            })

        except User.DoesNotExist:
            return Response(
                {"error": "Agent destinataire non trouvé"},
                status=status.HTTP_404_NOT_FOUND
            )
        except Account.DoesNotExist:
            return Response(
                {"error": "Compte non trouvé pour l'agent"},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    # ============================================================
    # 5. RETRAIT PARTENAIRE VIA AGENT (avec bénéficiaire)
    # ============================================================
    @action(detail=False, methods=['post'])
    def withdraw(self, request):
        """
        Agent enregistre un retrait partenaire avec informations du bénéficiaire
        """
        if request.user.role != 'agent':
            return Response(
                {"error": "Seul un agent peut enregistrer un retrait"},
                status=403
            )

        serializer = WithdrawalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            partner = Partner.objects.get(
                id=serializer.validated_data['partner_id']
            )

            recipient_data = None

            # Cas 1: Bénéficiaire existant
            if serializer.validated_data.get('recipient_id'):
                try:
                    recipient = WithdrawalRecipient.objects.get(
                        id=serializer.validated_data['recipient_id']
                    )
                    recipient_data = {'recipient_id': recipient.id}
                except WithdrawalRecipient.DoesNotExist:
                    return Response(
                        {"error": "Bénéficiaire non trouvé"},
                        status=404
                    )

            # Cas 2: Nouveau bénéficiaire
            elif serializer.validated_data.get('recipient_first_name'):
                recipient_data = {
                    'recipient_first_name': serializer.validated_data.get('recipient_first_name'),
                    'recipient_last_name': serializer.validated_data.get('recipient_last_name'),
                    'recipient_phone': serializer.validated_data.get('recipient_phone'),
                    'recipient_email': serializer.validated_data.get('recipient_email', ''),
                    'recipient_document_type': serializer.validated_data.get(
                        'recipient_document_type', 'cni'
                    ),
                    'recipient_document_number': serializer.validated_data.get(
                        'recipient_document_number'
                    ),
                    'recipient_address': serializer.validated_data.get(
                        'recipient_address', ''
                    ),
                }

            # Cas 3: Aucun bénéficiaire spécifié
            else:
                return Response(
                    {"error": "Veuillez spécifier un bénéficiaire (existant ou nouveau)"},
                    status=400
                )

            new_balance, transaction = FinanceService.withdraw_partner_via_agent(
                partner,
                request.user,
                serializer.validated_data['amount'],
                serializer.validated_data.get('description', ''),
                recipient_data
            )

            response_data = {
                "message": "Retrait effectué avec succès",
                "partner_balance": new_balance,
                "transaction_id": transaction.id,
                "amount": str(transaction.amount),
                "created_at": transaction.created_at
            }

            if transaction.recipient:
                response_data["recipient"] = {
                    "id": transaction.recipient.id,
                    "name": transaction.recipient.full_name,
                    "phone": transaction.recipient.phone,
                    "document_number": transaction.recipient.document_number
                }

            return Response(response_data)

        except Partner.DoesNotExist:
            return Response({"error": "Partenaire non trouvé"}, status=404)
        except ValidationError as e:
            return Response({"error": str(e)}, status=400)
        except Exception as e:
            return Response({"error": str(e)}, status=400)

    # ============================================================
    # 6. SOLDE D'UN PARTENAIRE
    # ============================================================
    @action(detail=False, methods=['get'], url_path='partner-balance')
    def partner_balance(self, request):
        """Récupère le solde d'un partenaire spécifique"""
        partner_id = request.query_params.get('partner_id')
        if not partner_id:
            return Response(
                {"error": "Le paramètre partner_id est requis"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            partner = Partner.objects.get(id=partner_id)
        except Partner.DoesNotExist:
            return Response(
                {"error": "Partenaire non trouvé"},
                status=status.HTTP_404_NOT_FOUND
            )

        user = request.user
        if user.role not in ['admin', 'agent']:
            return Response(
                {"error": "Non autorisé"},
                status=status.HTTP_403_FORBIDDEN
            )

        partner_account = Account.objects.filter(
            partner=partner, account_type='partner'
        ).first()

        if not partner_account:
            return Response(
                {"error": "Ce partenaire n'a pas encore de compte"},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response({
            "partner_id": partner.id,
            "partner_name": partner.name,
            "balance": partner_account.balance,
            "currency": partner_account.currency,
            "account_id": partner_account.id
        })

    # ============================================================
    # 7. SOLDE DE L'AGENT CONNECTÉ
    # ============================================================
    @action(detail=False, methods=['get'], url_path='agent-balance')
    def agent_balance(self, request):
        """Récupère le solde de l'agent connecté"""
        user = request.user

        if user.role != 'agent':
            return Response(
                {"error": "Seul un agent peut voir son solde"},
                status=status.HTTP_403_FORBIDDEN
            )

        agent_account = Account.objects.filter(
            user=user, account_type='agent'
        ).first()

        if not agent_account:
            return Response(
                {"error": "Vous n'avez pas encore de compte"},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response({
            "balance": agent_account.balance,
            "currency": agent_account.currency,
            "account_id": agent_account.id,
            "created_at": agent_account.created_at
        })

    # ============================================================
    # 8. LISTE DES BÉNÉFICIAIRES
    # ============================================================
    @action(detail=False, methods=['get'], url_path='recipients')
    def list_recipients(self, request):
        """Liste tous les bénéficiaires de retraits"""
        if request.user.role not in ['admin', 'agent']:
            return Response(
                {"error": "Non autorisé"},
                status=status.HTTP_403_FORBIDDEN
            )

        queryset = WithdrawalRecipient.objects.all().order_by('-created_at')

        search = request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(phone__icontains=search) |
                Q(email__icontains=search) |
                Q(document_number__icontains=search)
            )

        limit = request.query_params.get('limit')
        if limit:
            try:
                queryset = queryset[:int(limit)]
            except ValueError:
                pass

        if request.query_params.get('simple') == 'true':
            serializer = WithdrawalRecipientSimpleSerializer(
                queryset, many=True)
        else:
            serializer = WithdrawalRecipientSerializer(queryset, many=True)

        return Response(serializer.data)

    # ============================================================
    # 9. CRÉER UN BÉNÉFICIAIRE
    # ============================================================
    @action(detail=False, methods=['post'], url_path='recipients/create')
    def create_recipient(self, request):
        """Créer un nouveau bénéficiaire"""
        if request.user.role not in ['admin', 'agent']:
            return Response(
                {"error": "Non autorisé"},
                status=status.HTTP_403_FORBIDDEN
            )

        serializer = WithdrawalRecipientSerializer(data=request.data)
        if serializer.is_valid():
            recipient = serializer.save()
            return Response(
                WithdrawalRecipientSerializer(recipient).data,
                status=status.HTTP_201_CREATED
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    # ============================================================
    # 10. STATISTIQUES DES RETRAITS
    # ============================================================
    @action(detail=False, methods=['get'], url_path='withdrawal-stats')
    def withdrawal_stats(self, request):
        """Statistiques des retraits (admin uniquement)"""
        if request.user.role != 'admin':
            return Response(
                {"error": "Seul un administrateur peut voir les statistiques"},
                status=status.HTTP_403_FORBIDDEN
            )

        total_withdrawals = Transaction.objects.filter(
            transaction_type='withdrawal')
        total_count = total_withdrawals.count()
        total_amount = total_withdrawals.aggregate(Sum('amount'))[
            'amount__sum'] or 0

        top_recipients = WithdrawalRecipient.objects.annotate(
            total_withdrawals=Count('transactions'),
            total_amount=Sum('transactions__amount')
        ).filter(total_withdrawals__gt=0).order_by('-total_withdrawals')[:10]

        withdrawals_by_month = total_withdrawals.annotate(
            month=TruncMonth('created_at')
        ).values('month').annotate(
            count=Count('id'),
            total=Sum('amount')
        ).order_by('-month')

        return Response({
            "total_withdrawals": total_count,
            "total_amount": total_amount,
            "average_amount": total_amount / total_count if total_count > 0 else 0,
            "top_recipients": [
                {
                    "id": r.id,
                    "name": r.full_name,
                    "phone": r.phone,
                    "total_withdrawals": r.total_withdrawals,
                    "total_amount": r.total_amount
                }
                for r in top_recipients
            ],
            "by_month": [
                {
                    "month": item['month'].strftime('%Y-%m') if item['month'] else None,
                    "count": item['count'],
                    "total": item['total']
                }
                for item in withdrawals_by_month
            ]
        })

    # ============================================================
    # 11. STATISTIQUES GLOBALES
    # ============================================================
    @action(detail=False, methods=['get'], url_path='global-stats')
    def global_stats(self, request):
        """Statistiques globales des transactions (admin uniquement)"""
        if request.user.role != 'admin':
            return Response(
                {"error": "Seul un administrateur peut voir les statistiques"},
                status=status.HTTP_403_FORBIDDEN
            )

        type_stats = Transaction.objects.values('transaction_type').annotate(
            count=Count('id'),
            total=Sum('amount')
        ).order_by('transaction_type')

        today = timezone.now().date()
        today_transactions = Transaction.objects.filter(created_at__date=today)

        week_ago = timezone.now() - timedelta(days=7)
        week_transactions = Transaction.objects.filter(
            created_at__gte=week_ago)

        return Response({
            "by_type": [
                {
                    "type": item['transaction_type'],
                    "label": dict(Transaction.TRANSACTION_TYPES).get(item['transaction_type'], item['transaction_type']),
                    "count": item['count'],
                    "total": item['total']
                }
                for item in type_stats
            ],
            "today": {
                "count": today_transactions.count(),
                "total": today_transactions.aggregate(Sum('amount'))['amount__sum'] or 0
            },
            "this_week": {
                "count": week_transactions.count(),
                "total": week_transactions.aggregate(Sum('amount'))['amount__sum'] or 0
            }
        })

    # ============================================================
    # 12. EXPORTER LES TRANSACTIONS (CSV)
    # ============================================================
    @action(detail=False, methods=['get'], url_path='export')
    def export_transactions(self, request):
        """Exporte les transactions au format CSV"""
        if request.user.role != 'admin':
            return Response(
                {"error": "Seul un administrateur peut exporter les transactions"},
                status=status.HTTP_403_FORBIDDEN
            )

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
        writer.writerow([
            'ID', 'Type', 'Montant', 'De', 'Vers', 'Description',
            'Bénéficiaire', 'Téléphone bénéficiaire', 'Créé par', 'Date'
        ])

        for t in queryset:
            writer.writerow([
                t.id,
                t.get_transaction_type_display(),
                str(t.amount),
                str(t.from_account),
                str(t.to_account),
                t.description,
                t.recipient_name or '',
                t.recipient_phone or '',
                t.created_by.email if t.created_by else 'Système',
                t.created_at.strftime('%Y-%m-%d %H:%M:%S')
            ])

        return response

    # ============================================================
    # 13. TRANSACTIONS D'UN PARTENAIRE SPÉCIFIQUE
    # ============================================================
    @action(detail=False, methods=['get'], url_path='by-partner')
    def by_partner(self, request):
        """Récupère toutes les transactions d'un partenaire spécifique"""
        partner_id = request.query_params.get('partner_id')
        if not partner_id:
            return Response(
                {"error": "Le paramètre partner_id est requis"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            partner = Partner.objects.get(id=partner_id)
        except Partner.DoesNotExist:
            return Response(
                {"error": "Partenaire non trouvé"},
                status=status.HTTP_404_NOT_FOUND
            )

        user = request.user
        if user.role not in ['admin', 'agent']:
            return Response(
                {"error": "Non autorisé"},
                status=status.HTTP_403_FORBIDDEN
            )

        partner_account = Account.objects.filter(
            partner=partner, account_type='partner'
        ).first()

        if not partner_account:
            return Response(
                {"error": "Ce partenaire n'a pas encore de compte"},
                status=status.HTTP_404_NOT_FOUND
            )

        transactions = Transaction.objects.filter(
            Q(from_account=partner_account) | Q(to_account=partner_account)
        ).order_by('-created_at')

        limit = request.query_params.get('limit', 50)
        try:
            transactions = transactions[:int(limit)]
        except ValueError:
            transactions = transactions[:50]

        serializer = TransactionSerializer(transactions, many=True)
        return Response({
            "partner": {
                "id": partner.id,
                "name": partner.name,
                "balance": partner_account.balance
            },
            "transactions": serializer.data,
            "count": transactions.count()
        })


class AgentBalanceViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet pour la gestion des soldes des agents
    """
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        """
        Liste tous les agents avec leurs soldes

        🔓 Accessible par:
        - Admin: voit tous les agents
        - Agent: voit tous les autres agents (pour transfert entre agents)
        """
        user = request.user

        if not user.is_authenticated:
            return Response(
                {"error": "Authentication requise"},
                status=status.HTTP_401_UNAUTHORIZED
            )

        agents = User.objects.filter(role='agent')
        result = []

        for agent in agents:
            account = Account.objects.filter(
                user=agent, account_type='agent').first()

            # Si c'est un agent, ne pas inclure son propre compte dans la liste
            if user.role == 'agent' and agent.id == user.id:
                continue

            result.append({
                "id": agent.id,
                "email": agent.email,
                "full_name": agent.get_full_name(),
                "username": agent.username,
                "phone_number": agent.phone_number,
                "balance": account.balance if account else 0,
                "account_id": account.id if account else None,
                "currency": account.currency if account else 'XOF',
                "is_active": agent.is_active,
                "is_online": getattr(agent, 'is_online', False),
                "created_at": agent.created_at if hasattr(agent, 'created_at') else None,
                "last_login": agent.last_login
            })

        return Response(result)

    @action(detail=False, methods=['get'], url_path='me')
    def my_balance(self, request):
        """Récupère le solde de l'agent connecté"""
        user = request.user

        if user.role != 'agent':
            return Response(
                {"error": "Seul un agent peut voir son propre solde"},
                status=status.HTTP_403_FORBIDDEN
            )

        account = Account.objects.filter(
            user=user, account_type='agent').first()

        if not account:
            return Response(
                {"error": "Vous n'avez pas encore de compte"},
                status=status.HTTP_404_NOT_FOUND
            )

        return Response({
            "id": user.id,
            "email": user.email,
            "full_name": user.get_full_name(),
            "balance": account.balance,
            "currency": account.currency,
            "account_id": account.id,
            "created_at": account.created_at
        })

    @action(detail=True, methods=['get'], url_path='balance')
    def agent_balance_detail(self, request, pk=None):
        """Récupère le solde d'un agent spécifique"""
        user = request.user

        if user.role not in ['admin', 'agent']:
            return Response(
                {"error": "Non autorisé"},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            agent = User.objects.get(pk=pk, role='agent')
        except User.DoesNotExist:
            return Response(
                {"error": "Agent non trouvé"},
                status=status.HTTP_404_NOT_FOUND
            )

        if user.role == 'agent' and agent.id != user.id:
            return Response(
                {"error": "Vous ne pouvez voir que votre propre solde"},
                status=status.HTTP_403_FORBIDDEN
            )

        account = Account.objects.filter(
            user=agent, account_type='agent').first()

        return Response({
            "id": agent.id,
            "email": agent.email,
            "full_name": agent.get_full_name(),
            "balance": account.balance if account else 0,
            "account_id": account.id if account else None,
            "currency": account.currency if account else 'XOF'
        })


class StatisticsViewSet(viewsets.ViewSet):
    """
    ViewSet pour les statistiques globales (admin uniquement)
    """
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        if request.user.role != 'admin':
            return Response(
                {"error": "Seul un administrateur peut voir les statistiques"},
                status=status.HTTP_403_FORBIDDEN
            )

        total_partners = Partner.objects.count()
        total_agents = User.objects.filter(role='agent').count()

        global_account = FinanceService.get_global_account()

        partner_accounts = Account.objects.filter(account_type='partner')
        agent_accounts = Account.objects.filter(account_type='agent')

        total_partner_balance = sum(acc.balance for acc in partner_accounts)
        total_agent_balance = sum(acc.balance for acc in agent_accounts)

        total_transactions = Transaction.objects.count()
        deposit_transactions = Transaction.objects.filter(
            transaction_type='deposit').count()
        transfer_transactions = Transaction.objects.filter(
            transaction_type='transfer_to_agent').count()
        withdrawal_transactions = Transaction.objects.filter(
            transaction_type='withdrawal').count()

        from django.db.models import Sum
        total_amount = Transaction.objects.aggregate(Sum('amount'))[
            'amount__sum'] or 0

        return Response({
            "partners": {
                "total": total_partners,
                "total_balance": total_partner_balance
            },
            "agents": {
                "total": total_agents,
                "total_balance": total_agent_balance
            },
            "global_account": {
                "balance": global_account.balance if global_account else 0,
                "currency": global_account.currency if global_account else 'XOF'
            },
            "transactions": {
                "total": total_transactions,
                "deposits": deposit_transactions,
                "transfers": transfer_transactions,
                "withdrawals": withdrawal_transactions,
                "total_amount": total_amount
            }
        })
