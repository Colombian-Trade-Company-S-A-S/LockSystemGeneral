from django.urls import path
from rest_framework.routers import DefaultRouter

from .diagnostico import DiagnosticoApiView
from .registros import PincodesUsadosView, SincronizacionesView
from .views import TelevisorViewSet

router = DefaultRouter()
router.register(r'televisores', TelevisorViewSet, basename='televisor')

urlpatterns = [
    path('sincronizaciones/', SincronizacionesView.as_view(), name='sincronizaciones'),
    path('pincodes/', PincodesUsadosView.as_view(), name='pincodes'),
    path('diagnostico-api/', DiagnosticoApiView.as_view(), name='diagnostico-api'),
    *router.urls,
]
