
from django.contrib import admin
from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import *

router = DefaultRouter()
router.register('partners', PartnerViewSet, basename='partner')
router.register('accounts', AccountViewSet, basename='account')
router.register('transactions', TransactionViewSet, basename='transaction')


urlpatterns = router.urls
