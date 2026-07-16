# urls.py
from django.contrib import admin
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import *

router = DefaultRouter()
router.register('partners', PartnerViewSet, basename='partner')
router.register('accounts', AccountViewSet, basename='account')
router.register('transactions', TransactionViewSet, basename='transaction')
# Ajoutez cette ligne pour les agents
router.register('agents-balance', AgentBalanceViewSet,
                basename='agent-balance')
router.register('recipients', WithdrawalRecipientViewSet, basename='recipient')

urlpatterns = router.urls
