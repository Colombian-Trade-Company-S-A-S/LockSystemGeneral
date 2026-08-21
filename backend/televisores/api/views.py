from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response

from empresas.scoping import EmpresaScopedViewSetMixin, acotar
from televisores.models import Televisor
from users.permissions import CanOperate, IsNotGlobalAdmin
from televisores.portal.client import (
    PortalClient,
    PortalDispositivoNoExiste,
    PortalError,
)

from .imports import importar_televisores


def client_ip(request) -> str | None:
    """IP del cliente, considerando un posible proxy inverso (X-Forwarded-For)."""
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    if xff:
        return xff.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


def usuario_para_auditoria(request):
    """Usuario a registrar en la auditoría, o None.

    Las peticiones por API-key llegan con un usuario sintético (sin fila en la
    base): no puede guardarse como llave foránea, así que esas acciones quedan
    con usuario vacío (la trazabilidad va por la API-key y la IP). Solo se
    devuelve un usuario cuando es una cuenta real y persistida."""
    from django.contrib.auth import get_user_model

    user = getattr(request, 'user', None)
    return user if isinstance(user, get_user_model()) and user.pk else None
from .serializers import (
    BulkSyncJobSerializer,
    PinCodeUsadoSerializer,
    SyncJobSerializer,
    TelevisorSerializer,
)


class TelevisorViewSet(EmpresaScopedViewSetMixin, viewsets.ModelViewSet):
    """CRUD de televisores + acciones contra el portal WhaleTV.

    Todo lo de aquí está acotado a la empresa del usuario (ver
    EmpresaScopedViewSetMixin): el queryset se filtra y lo que se crea se sella
    con su empresa. Pedir un televisor de otra empresa devuelve 404.

    - list / retrieve / create / update / destroy estándar.
    - `?search=` busca por MAC, serial o número de crédito (SearchFilter global).
    - `importar/`        carga masiva desde CSV/XLSX.
    - `estado-portal/`   (GET)  lee el estado real del dispositivo en el portal.
    - `habilitar/`       (POST) desbloquea el dispositivo en el portal (unlock).
    - `inhabilitar/`     (POST) marca inhabilitado localmente (ver nota).
    """

    queryset = Televisor.objects.select_related('empresa')
    serializer_class = TelevisorSerializer
    search_fields = ['mac_address', 'serial_number', 'numero_credito']
    ordering_fields = ['mac_address', 'serial_number', 'created_at']

    # Acciones de escritura/gestión reservadas a Operador y Administrador.
    # El resto (consultas, validación, pines, reportes) queda para cualquier
    # usuario autenticado, incluido el rol Consulta.
    OPERATOR_ACTIONS = frozenset({
        'create',
        'update',
        'partial_update',
        'destroy',
        'importar',                  # Enrolar Televisores (masivo)
        'enrolar_estado',            # Enrolar Estado (masivo)
        'enrolar_estado_cancelar',   # Cancelar sincronización masiva
        'enrolar_estado_exportar',   # Exportar sincronización masiva a Excel
        'estado',                    # Habilitar / Inhabilitar (sincroniza al portal)
    })

    # Acción que el rol Consulta sí puede hacer, pero que muta (entrega un pin y
    # lo marca como usado en el portal). El administrador general queda fuera:
    # es un auditor de solo lectura. El resto de mutaciones ya están en
    # OPERATOR_ACTIONS, que CanOperate también le niega.
    ACCIONES_NO_ADMIN_GLOBAL = frozenset({'pincode_usar'})

    def get_permissions(self):
        if self.action in self.OPERATOR_ACTIONS:
            return [permissions.IsAuthenticated(), CanOperate()]
        if self.action in self.ACCIONES_NO_ADMIN_GLOBAL:
            return [permissions.IsAuthenticated(), IsNotGlobalAdmin()]
        return [permissions.IsAuthenticated()]

    @action(
        detail=False,
        methods=['post'],
        url_path='importar',
        parser_classes=[MultiPartParser, FormParser],
    )
    def importar(self, request):
        archivo = request.FILES.get('archivo')
        if not archivo:
            return Response(
                {'detail': 'Debes adjuntar un archivo en el campo "archivo".'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        resultado = importar_televisores(
            archivo.name, archivo.read(), empresa=self.empresa_destino()
        )
        return Response(resultado, status=status.HTTP_200_OK)

    # ------------------------------------------------------------------
    # Integración con el portal WhaleTV (Device Lock API)
    # ------------------------------------------------------------------
    def _leer_estado_portal(self, tv: Televisor) -> Response | dict:
        try:
            data = PortalClient().get_status(tv.eui64_portal)
        except PortalDispositivoNoExiste as e:
            return Response({'detail': str(e)}, status=status.HTTP_404_NOT_FOUND)
        except PortalError as e:
            return Response({'detail': str(e)}, status=status.HTTP_502_BAD_GATEWAY)
        return {
            'eui64': tv.eui64_portal,
            'lock_status': data['lockStatus'],       # 0=desbloqueado, 1=bloqueado
            'payment_status': data['paymentStatus'],  # 0=en progreso, 1=completado
            'clear_status': data['clearStatus'],      # 0=normal, 1=limpiando
            'inhabilitado_portal': data['lockStatus'] == 1,
        }

    @action(detail=True, methods=['get'], url_path='estado-portal')
    def estado_portal(self, request, pk=None):
        resultado = self._leer_estado_portal(self.get_object())
        if isinstance(resultado, Response):
            return resultado
        return Response(resultado)

    @action(detail=True, methods=['get'])
    def validar(self, request, pk=None):
        """Valida (dry-run) el estado del TV: lee el portal y lo compara con el
        estado local, sin modificar nada."""
        tv = self.get_object()
        resultado = self._leer_estado_portal(tv)
        if isinstance(resultado, Response):
            return resultado

        remoto = bool(resultado['inhabilitado_portal'])
        local = bool(tv.inhabilitado)
        coincide = remoto == local
        txt = 'Inhabilitado' if remoto else 'Habilitado'
        txt_local = 'Inhabilitado' if local else 'Habilitado'
        mensaje = (
            f'El televisor está {txt} en el portal, igual que en la app. '
            'No hay nada que sincronizar.'
            if coincide
            else f'El televisor está {txt} en el portal, pero {txt_local} en la app. '
            'Conviene sincronizar para que coincidan.'
        )
        return Response({
            'coincide': coincide,
            'remoto_inhabilitado': remoto,
            'local_inhabilitado': local,
            'mensaje': mensaje,
        })

    def _lanzar_validacion_masiva(self, request, empresa):
        """Panel: valida TODOS los televisores de la empresa. La API de
        integración lo sobreescribe para validar SOLO los seriales del JSON."""
        from televisores.bulk_sync import lanzar_validacion_masiva

        return lanzar_validacion_masiva(empresa=empresa)

    @action(detail=False, methods=['post'], url_path='validar-masivo')
    def validar_masivo(self, request):
        """Lanza una validación masiva (dry-run) de los televisores de la empresa."""
        job = self._lanzar_validacion_masiva(request, self.empresa_destino())
        if job is None:
            return Response(
                {'detail': 'No hay televisores para validar.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(
            {'job': job.pk, 'total': job.total}, status=status.HTTP_202_ACCEPTED
        )

    # ------------------------------------------------------------------
    # Lotes (BulkSyncJob): siempre se buscan dentro de la empresa del usuario.
    # Un job es un id secuencial y adivinable, así que sin este filtro bastaría
    # con probar números para leer los lotes (y las MAC) de otra empresa.
    # ------------------------------------------------------------------
    def _buscar_bulk_job(self, job_id):
        from televisores.models import BulkSyncJob

        return (
            acotar(BulkSyncJob.objects.all(), self.request.user)
            # items__televisor: el serializer del item saca el serial del
            # televisor; sin este prefetch sería una consulta por item.
            .prefetch_related('items__televisor')
            .filter(pk=job_id)
            .first()
        )

    @action(
        detail=False,
        methods=['get'],
        url_path=r'validar-masivo/(?P<job_id>[0-9]+)',
    )
    def validar_masivo_status(self, request, job_id=None):
        """Progreso/resultado de una validación masiva (polling)."""
        job = self._buscar_bulk_job(job_id)
        if job is None:
            return Response(
                {'detail': 'Job no encontrado.'}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(BulkSyncJobSerializer(job).data)

    @action(
        detail=False,
        methods=['post'],
        url_path=r'validar-masivo/(?P<job_id>[0-9]+)/cancelar',
    )
    def validar_masivo_cancelar(self, request, job_id=None):
        return self._cancelar_bulk_job(job_id)

    @action(
        detail=False,
        methods=['get'],
        url_path=r'validar-masivo/(?P<job_id>[0-9]+)/exportar',
    )
    def validar_masivo_exportar(self, request, job_id=None):
        return self._exportar_bulk_job(job_id)

    @action(detail=True, methods=['get'])
    def pincodes(self, request, pk=None):
        """Grupos de Pin Code disponibles del dispositivo (passCode + pinCode)."""
        tv = self.get_object()
        try:
            grupos = PortalClient().get_pin_codes(tv.eui64_portal)
        except PortalDispositivoNoExiste as e:
            return Response({'detail': str(e)}, status=status.HTTP_404_NOT_FOUND)
        except PortalError as e:
            return Response({'detail': str(e)}, status=status.HTTP_502_BAD_GATEWAY)
        return Response({'eui64': tv.eui64_portal, 'grupos': grupos})

    @action(detail=True, methods=['post'], url_path='pincodes/usar')
    def pincode_usar(self, request, pk=None):
        """Obtiene el Código Pin para un Código de Acceso, lo marca como usado en
        el portal y lo registra en la bitácora (aparece en /pincodes)."""
        from televisores.models import PinCodeUsado

        tv = self.get_object()
        passcode = str(request.data.get('passcode', '')).strip()
        if not passcode:
            return Response(
                {'detail': 'Debes enviar "passcode".'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        client = PortalClient()
        try:
            grupos = client.get_pin_codes(tv.eui64_portal)
        except PortalDispositivoNoExiste as e:
            return Response({'detail': str(e)}, status=status.HTTP_404_NOT_FOUND)
        except PortalError as e:
            return Response({'detail': str(e)}, status=status.HTTP_502_BAD_GATEWAY)

        grupo = next((g for g in grupos if g['passCode'] == passcode), None)
        if grupo is None:
            return Response(
                {'detail': 'No hay un Código Pin disponible para ese Código de Acceso.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Marca el código como usado en el portal (no bloquea si falla).
        try:
            client.marcar_pincodes_usados(tv.eui64_portal, [passcode])
        except PortalError:
            pass

        registro = PinCodeUsado.objects.create(
            empresa=tv.empresa,
            televisor=tv,
            mac_address=tv.mac_address,
            passcode=passcode,
            pin_code=grupo['pinCode'],
            usuario=usuario_para_auditoria(request),
            ip=client_ip(request),
        )
        return Response({
            'passcode': passcode,
            'pin_code': grupo['pinCode'],
            'creado': registro.creado,
        })

    @action(detail=True, methods=['post'])
    def estado(self, request, pk=None):
        """Guarda el estado local (habilitado/inhabilitado) y lanza la sync al
        portal en segundo plano. Devuelve el id del job para hacer polling.

        Body: {"inhabilitar": true|false}
        """
        from televisores.sync_runner import lanzar_sync_job

        tv = self.get_object()
        inhabilitar = bool(request.data.get('inhabilitar'))

        # El estado local es la fuente de verdad; se guarda de inmediato (whaletv).
        tv.inhabilitado = inhabilitar
        tv.save(update_fields=['inhabilitado'])

        job = lanzar_sync_job(
            tv, inhabilitar, usuario=usuario_para_auditoria(request), ip=client_ip(request)
        )
        return Response(
            {
                'job': job.pk,
                'estado': job.estado,
                'inhabilitado': tv.inhabilitado,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @action(
        detail=True,
        methods=['get'],
        url_path=r'sync/(?P<job_id>[0-9]+)',
    )
    def sync_status(self, request, pk=None, job_id=None):
        """Estado/progreso de un SyncJob (para polling desde el frontend)."""
        from televisores.models import SyncJob

        # get_object() resuelve el televisor por la columna que use el viewset
        # (PK en el panel, serial en integración) y ya viene acotado por empresa.
        tv = self.get_object()
        job = SyncJob.objects.filter(pk=job_id, televisor=tv).first()
        if job is None:
            return Response(
                {'detail': 'Job no encontrado.'}, status=status.HTTP_404_NOT_FOUND
            )
        return Response({
            'job': job.pk,
            'estado': job.estado,
            'porcentaje': job.porcentaje,
            'finalizado': job.finalizado,
            'error': job.error,
            'inhabilitar': job.inhabilitar,
        })

    @action(detail=True, methods=['get'])
    def historial(self, request, pk=None):
        """Histórico de cambios de estado (SyncJobs) del televisor."""
        tv = self.get_object()
        jobs = tv.sync_jobs.all()[:50]
        return Response(SyncJobSerializer(jobs, many=True).data)

    # ------------------------------------------------------------------
    # Registros del televisor (sincronizaciones y códigos pin usados)
    # ------------------------------------------------------------------
    @action(detail=True, methods=['get'])
    def registros(self, request, pk=None):
        """Contadores para la sección 'Registros' del detalle."""
        from televisores.models import BulkSyncItem, BulkSyncJob, PinCodeUsado, SyncJob

        tv = self.get_object()
        sinc = (
            SyncJob.objects.filter(televisor=tv).count()
            + BulkSyncItem.objects.filter(
                televisor=tv, job__modo=BulkSyncJob.SYNC
            ).count()
        )
        pins = PinCodeUsado.objects.filter(televisor=tv).count()
        return Response({'sincronizaciones': sinc, 'pincodes': pins})

    @action(detail=True, methods=['get'])
    def sincronizaciones(self, request, pk=None):
        """Historial de sincronizaciones de ESTE televisor (paginado)."""
        from .registros import _fila, qs_sincronizaciones

        qs = qs_sincronizaciones(televisor=self.get_object())
        page = self.paginate_queryset(qs)
        return self.get_paginated_response([_fila(r) for r in page])

    @action(detail=True, methods=['get'], url_path='pincodes-usados')
    def pincodes_usados(self, request, pk=None):
        """Códigos Pin usados de ESTE televisor (paginado)."""
        from televisores.models import PinCodeUsado

        tv = self.get_object()
        qs = PinCodeUsado.objects.filter(televisor=tv)
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(
            PinCodeUsadoSerializer(page, many=True).data
        )

    @action(detail=True, methods=['get'], url_path='exportar-sincronizaciones')
    def exportar_sincronizaciones_tv(self, request, pk=None):
        """Excel con TODAS las sincronizaciones de ESTE televisor (sin paginar)."""
        from televisores.api.exports import exportar_sincronizaciones

        return exportar_sincronizaciones(televisor=self.get_object())

    @action(detail=True, methods=['get'], url_path='exportar-pincodes')
    def exportar_pincodes_tv(self, request, pk=None):
        """Excel con TODOS los Códigos Pin usados de ESTE televisor."""
        from televisores.api.exports import exportar_pincodes

        return exportar_pincodes(televisor=self.get_object())

    # ------------------------------------------------------------------
    # Enrolar Estado: cambio de estado masivo + sincronización al portal
    # ------------------------------------------------------------------
    def _resumen_enrolar_masivo(self, request, empresa):
        """Procesa la entrada del enrolar-estado y devuelve el resumen (o None si
        no vino ni archivo ni registros).

        El PANEL identifica cada televisor por su MAC (archivo CSV/XLSX o JSON) y
        puede crear equipos nuevos. La API de integración sobreescribe este método
        para identificar por SERIAL (ver IntegracionTelevisorViewSet)."""
        from televisores.estado_import import (
            procesar_enrolar_estado,
            procesar_registros,
        )

        archivo = request.FILES.get('archivo')
        if archivo is not None:
            return procesar_enrolar_estado(
                archivo.name, archivo.read(), empresa=empresa
            )
        if 'registros' in request.data:
            return procesar_registros(request.data.get('registros'), empresa=empresa)
        return None

    @action(
        detail=False,
        methods=['post'],
        url_path='enrolar-estado',
        parser_classes=[JSONParser, MultiPartParser, FormParser],
    )
    def enrolar_estado(self, request):
        """Aplica estados (habilitado/inhabilitado) masivamente y lanza la
        sincronización con el portal en 2º plano. Devuelve el resumen + el id del
        job masivo. Acepta los datos de DOS formas:

        - JSON (integraciones/ERP): {"registros": [{"mac_address","estado",
          "serial_number"?}, ...]}. No sube ningún archivo.
        - Archivo (panel): campo multipart `archivo` con un CSV/XLSX.
        """
        from televisores.bulk_sync import lanzar_bulk_job

        empresa = self.empresa_destino()
        resumen = self._resumen_enrolar_masivo(request, empresa)
        if resumen is None:
            return Response(
                {'detail': 'Envía "registros" (JSON) o un archivo en el campo '
                 '"archivo".'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        cambiados = resumen['cambiados']

        job = (
            lanzar_bulk_job(
                cambiados,
                resumen,
                empresa=empresa,
                usuario=usuario_para_auditoria(request),
                ip=client_ip(request),
            )
            if cambiados
            else None
        )
        return Response(
            {
                'job': job.pk if job else None,
                'creados': resumen['creados'],
                'actualizados': resumen['actualizados'],
                'cambios': len(cambiados),
                'errores': resumen['errores'],
            },
            status=status.HTTP_202_ACCEPTED,
        )

    # ------------------------------------------------------------------
    # Exportaciones a Excel (.xlsx)
    # ------------------------------------------------------------------
    @action(detail=False, methods=['get'], url_path='exportar-sincronizaciones')
    def exportar_sincronizaciones(self, request):
        from televisores.api.exports import exportar_sincronizaciones
        return exportar_sincronizaciones(
            request.query_params.get('desde'),
            request.query_params.get('hasta'),
            user=request.user,
        )

    @action(detail=False, methods=['get'], url_path='exportar-pincodes')
    def exportar_pincodes(self, request):
        from televisores.api.exports import exportar_pincodes
        return exportar_pincodes(
            request.query_params.get('desde'),
            request.query_params.get('hasta'),
            user=request.user,
        )

    @action(detail=False, methods=['get'], url_path='plantilla-televisores')
    def plantilla_televisores(self, request):
        from televisores.api.exports import plantilla_televisores
        return plantilla_televisores()

    @action(detail=False, methods=['get'], url_path='plantilla-estados')
    def plantilla_estados(self, request):
        from televisores.api.exports import plantilla_estados
        return plantilla_estados()

    @action(
        detail=False,
        methods=['get'],
        url_path=r'enrolar-estado/(?P<job_id>[0-9]+)',
    )
    def enrolar_estado_status(self, request, job_id=None):
        """Progreso de una sincronización masiva (para polling)."""
        job = self._buscar_bulk_job(job_id)
        if job is None:
            return Response(
                {'detail': 'Job no encontrado.'}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(BulkSyncJobSerializer(job).data)

    @action(
        detail=False,
        methods=['post'],
        url_path=r'enrolar-estado/(?P<job_id>[0-9]+)/cancelar',
    )
    def enrolar_estado_cancelar(self, request, job_id=None):
        return self._cancelar_bulk_job(job_id)

    @action(
        detail=False,
        methods=['get'],
        url_path=r'enrolar-estado/(?P<job_id>[0-9]+)/exportar',
    )
    def enrolar_estado_exportar(self, request, job_id=None):
        return self._exportar_bulk_job(job_id)

    def _exportar_bulk_job(self, job_id):
        from televisores.api.exports import exportar_bulk_job

        job = self._buscar_bulk_job(job_id)
        if job is None:
            return Response(
                {'detail': 'Job no encontrado.'}, status=status.HTTP_404_NOT_FOUND
            )
        return exportar_bulk_job(job)

    def _cancelar_bulk_job(self, job_id):
        """Marca un BulkSyncJob (sync o validación) para que el hilo en
        segundo plano lo detenga en el próximo televisor que revise."""
        job = self._buscar_bulk_job(job_id)
        if job is None:
            return Response(
                {'detail': 'Job no encontrado.'}, status=status.HTTP_404_NOT_FOUND
            )
        if not job.finalizado:
            job.cancelar_solicitado = True
            job.save(update_fields=['cancelar_solicitado'])
        return Response(BulkSyncJobSerializer(job).data)
