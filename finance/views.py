from django.shortcuts import render

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

class AccountViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = AccountSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
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
        if user.role == 'admin':
            return Transaction.objects.all().order_by('-created_at')
        elif user.role == 'agent':
            agent_acc = Account.objects.filter(user=user, account_type='agent').first()
            if agent_acc:
                return Transaction.objects.filter(
                    models.Q(from_account=agent_acc) | models.Q(to_account=agent_acc)
                )
        return Transaction.objects.none()
    
    @action(detail=False, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def deposit(self, request):
        """Dépôt partenaire (admin ou agent ? selon process, généralement admin)"""
        if request.user.role not in ['admin', 'agent']:
            return Response({"error": "Non autorisé"}, status=403)
        serializer = DepositSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            partner = Partner.objects.get(id=serializer.validated_data['partner_id'])
            new_balance = FinanceService.deposit_partner(
                partner,
                serializer.validated_data['amount'],
                serializer.validated_data.get('description', '')
            )
            return Response({"message": "Dépôt effectué", "partner_balance": new_balance})
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
            agent = User.objects.get(id=serializer.validated_data['agent_id'], role='agent')
            new_balance = FinanceService.transfer_to_agent(
                agent,
                serializer.validated_data['amount'],
                serializer.validated_data.get('description', '')
            )
            return Response({"message": "Transfert effectué", "agent_balance": new_balance})
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
            partner = Partner.objects.get(id=serializer.validated_data['partner_id'])
            new_balance = FinanceService.withdraw_partner_via_agent(
                partner,
                request.user,
                serializer.validated_data['amount'],
                serializer.validated_data.get('description', '')
            )
            # Si vous préférez débiter le global, appelez withdraw_partner_via_agent_with_global_debit
            return Response({"message": "Retrait effectué", "partner_balance": new_balance})
        except Partner.DoesNotExist:
            return Response({"error": "Partenaire non trouvé"}, status=404)
        except Exception as e:
            return Response({"error": str(e)}, status=400)