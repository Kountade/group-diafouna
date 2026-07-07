from django.shortcuts import render
from django.db import models
from django.db.models import Q

# Create your views here.
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from .models import Partner, Account, Transaction
from .serializers import (
    PartnerSerializer, AccountSerializer, TransactionSerializer,
    DepositSerializer, TransferToAgentSerializer, WithdrawalSerializer
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

        transaction_type = self.request.query_params.get('transaction_type')
        if transaction_type:
            return queryset.filter(transaction_type=transaction_type)

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
        except Exception as e:
            return Response({"error": str(e)}, status=400)

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
        except Exception as e:
            return Response({"error": str(e)}, status=400)

    @action(detail=False, methods=['post'])
    def withdraw(self, request):
        """Agent enregistre un retrait partenaire"""
        if request.user.role != 'agent':
            return Response({"error": "Seul un agent peut enregistrer un retrait"}, status=403)
        serializer = WithdrawalSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            partner = Partner.objects.get(
                id=serializer.validated_data['partner_id'])
            new_balance = FinanceService.withdraw_partner_via_agent(
                partner,
                request.user,
                serializer.validated_data['amount'],
                serializer.validated_data.get('description', '')
            )
            return Response({
                "message": "Retrait effectué",
                "partner_balance": new_balance
            })
        except Partner.DoesNotExist:
            return Response({"error": "Partenaire non trouvé"}, status=404)
        except Exception as e:
            return Response({"error": str(e)}, status=400)

    @action(detail=False, methods=['get'], url_path='partner-balance')
    def partner_balance(self, request):
        """
        Récupère le solde d'un partenaire spécifique (admin ou agent)
        """
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
            partner=partner, account_type='partner').first()

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


class AgentBalanceViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet pour la gestion des soldes des agents
    """
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        """
        Liste tous les agents avec leurs soldes (admin uniquement)
        GET /api/agents-balance/
        """
        user = request.user

        if user.role != 'admin':
            return Response(
                {"error": "Seul un administrateur peut voir la liste complète des agents"},
                status=status.HTTP_403_FORBIDDEN
            )

        agents = User.objects.filter(role='agent')
        result = []

        for agent in agents:
            account = Account.objects.filter(
                user=agent, account_type='agent').first()
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
                "created_at": agent.created_at if hasattr(agent, 'created_at') else None,
                "last_login": agent.last_login
            })

        return Response(result)

    @action(detail=False, methods=['get'], url_path='me')
    def my_balance(self, request):
        """
        Récupère le solde de l'agent connecté
        GET /api/agents-balance/me/
        """
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
        """
        Récupère le solde d'un agent spécifique (admin uniquement)
        GET /api/agents-balance/{id}/balance/
        """
        user = request.user

        if user.role != 'admin':
            return Response(
                {"error": "Seul un administrateur peut voir le solde d'un autre agent"},
                status=status.HTTP_403_FORBIDDEN
            )

        try:
            agent = User.objects.get(pk=pk, role='agent')
        except User.DoesNotExist:
            return Response(
                {"error": "Agent non trouvé"},
                status=status.HTTP_404_NOT_FOUND
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
