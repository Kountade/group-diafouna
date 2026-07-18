from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    DashboardViewSet, DashboardPreferencesViewSet,
    DashboardAlertViewSet, DashboardWidgetViewSet,
    DashboardMetricViewSet
)

router = DefaultRouter()
router.register('dashboard', DashboardViewSet, basename='dashboard')
router.register('preferences', DashboardPreferencesViewSet,
                basename='preferences')
router.register('alerts', DashboardAlertViewSet, basename='alerts')
router.register('widgets', DashboardWidgetViewSet, basename='widgets')
router.register('metrics', DashboardMetricViewSet, basename='metrics')

urlpatterns = [
    path('', include(router.urls)),
]
