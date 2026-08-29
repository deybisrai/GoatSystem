from django import forms
from django.contrib import admin
from django.db.models import Case, DecimalField, F, IntegerField, Sum, When
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.urls import reverse
from django.utils.html import format_html

from .forms import ValidacionForm

admin.site.site_header = 'GOAT X'
admin.site.site_title = 'GOAT X'
admin.site.index_title = 'Administracion de la tienda'

from .models import (
    Atributo,
    Campana,
    Categoria,
    Cliente,
    Color,
    Compra,
    CompraDetalle,
    Cupon,
    Curva,
    CuentaRecaudadora,
    Inventario,
    MovimientoInventario,
    Pago,
    Pedido,
    PedidoDetalle,
    Producto,
    Proveedor,
    PuntoRecojo,
    Traslado,
    TrasladoDetalle,
    Ubicacion,
    ValorAtributo,
    Variante,
)


def circulo(tono, tamano=20):
    """ Un punto de color para mostrar en el admin """
    return format_html(
        '<span style="display:inline-block;width:{}px;height:{}px;border-radius:50%;'
        'border:1px solid rgba(0,0,0,.25);background:{}"></span>',
        tamano, tamano, tono,
    )


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'atributo', 'usa_genero', 'activo')
    list_editable = ('atributo', 'usa_genero', 'activo')
    prepopulated_fields = {'slug': ('nombre',)}


class ValorAtributoInline(admin.TabularInline):
    model = ValorAtributo
    extra = 3


@admin.register(Atributo)
class AtributoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'cuantos_valores', 'categorias_que_lo_usan')
    inlines = [ValorAtributoInline]

    @admin.display(description='Valores')
    def cuantos_valores(self, obj):
        return obj.valores.count()

    @admin.display(description='Usado en')
    def categorias_que_lo_usan(self, obj):
        nombres = list(obj.categorias.values_list('nombre', flat=True))
        return ', '.join(nombres) if nombres else '-'


@admin.register(ValorAtributo)
class ValorAtributoAdmin(admin.ModelAdmin):
    list_display = ('valor', 'atributo', 'orden')
    list_editable = ('orden',)
    list_filter = ('atributo',)
    search_fields = ('valor',)


@admin.register(Curva)
class CurvaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'atributo', 'categoria', 'genero', 'lista_valores')
    list_filter = ('atributo', 'categoria', 'genero')
    filter_horizontal = ('valores',)

    @admin.display(description='Valores')
    def lista_valores(self, obj):
        return ', '.join(str(v) for v in obj.valores_ordenados())

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        # al armar una curva de tallas no tiene sentido ofrecer capacidades de celular
        if db_field.name == 'valores':
            curva_id = request.resolver_match.kwargs.get('object_id')
            if curva_id:
                curva = Curva.objects.filter(pk=curva_id).first()
                if curva:
                    kwargs['queryset'] = ValorAtributo.objects.filter(atributo=curva.atributo)
        return super().formfield_for_manytomany(db_field, request, **kwargs)


class InventarioInline(admin.TabularInline):
    model = Inventario
    extra = 1
    fields = ('valor', 'precio_venta_override', 'stock', 'costo_promedio')
    readonly_fields = ('stock', 'costo_promedio')

    def get_formset(self, request, obj=None, **kwargs):
        # guarda la variante padre para poder acotar el desplegable de valores
        self._variante = obj
        return super().get_formset(request, obj, **kwargs)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        """ Solo ofrece los valores que aplican al producto (categoria + genero) """
        if db_field.name == 'valor':
            variante = getattr(self, '_variante', None)
            if variante is not None:
                kwargs['queryset'] = Inventario.valores_validos(variante.producto)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


@admin.register(Variante)
class VarianteAdmin(admin.ModelAdmin):
    list_display = ('sku', 'producto', 'color', 'muestra_color', 'precio_venta', 'cuantas_unidades', 'activo')
    list_editable = ('precio_venta', 'activo')
    list_filter = ('producto__categoria', 'color', 'activo')
    search_fields = ('sku', 'producto__nombre', 'color__nombre')
    inlines = [InventarioInline]
    actions = ['generar_desde_curva']

    @admin.display(description='Unidades')
    def cuantas_unidades(self, obj):
        return obj.items.count()

    @admin.action(description='Generar inventario desde una curva')
    def generar_desde_curva(self, request, queryset):
        """
        Crea de un golpe una fila de inventario por cada valor de la curva.
        Las que ya existan se respetan, asi que se puede volver a correr sin duplicar.
        """
        if 'aplicar' in request.POST:
            formulario = GenerarCurvaForm(request.POST, variantes=queryset)
            if formulario.is_valid():
                curva = formulario.cleaned_data['curva']
                creadas = omitidas = 0
                for variante in queryset:
                    for valor in curva.valores_ordenados():
                        _, nuevo = Inventario.objects.get_or_create(variante=variante, valor=valor)
                        if nuevo:
                            creadas += 1
                        else:
                            omitidas += 1
                self.message_user(
                    request,
                    f'{creadas} unidades creadas con la curva {curva.nombre}.'
                    + (f' {omitidas} ya existian y se respetaron.' if omitidas else '')
                )
                return None
        else:
            formulario = GenerarCurvaForm(variantes=queryset)

        return render(request, 'admin/generar_curva.html', {
            'variantes': queryset,
            'formulario': formulario,
            'accion': 'generar_desde_curva',
            'seleccionadas': request.POST.getlist(admin.helpers.ACTION_CHECKBOX_NAME),
        })

    @admin.display(description='Muestra')
    def muestra_color(self, obj):
        return circulo(obj.color.muestra)


@admin.register(Color)
class ColorAdmin(admin.ModelAdmin):
    """ La paleta de la tienda: cada color se define una vez y se reutiliza """
    list_display = ('nombre', 'muestra_color', 'muestra', 'cuantas_variantes')
    search_fields = ('nombre',)

    @admin.display(description='')
    def muestra_color(self, obj):
        return circulo(obj.muestra, 24)

    @admin.display(description='Usado en')
    def cuantas_variantes(self, obj):
        cuantas = obj.variantes.count()
        return f'{cuantas} variante(s)' if cuantas else '-'

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        # selector de color nativo, para no escribir codigos a mano
        if db_field.name == 'muestra':
            kwargs['widget'] = forms.TextInput(attrs={'type': 'color', 'style': 'width:60px;height:34px'})
        return super().formfield_for_dbfield(db_field, request, **kwargs)


class VarianteInline(admin.StackedInline):
    model = Variante
    extra = 1


class GenerarCurvaForm(forms.Form):
    """ Pide la curva con la que llenar el inventario de las variantes elegidas """
    curva = forms.ModelChoiceField(queryset=Curva.objects.all(), label='Curva')

    def __init__(self, *args, variantes=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not variantes:
            return
        # ofrece solo las curvas que aplican a la categoria y genero de lo seleccionado
        aplicables = Curva.objects.none()
        for variante in variantes:
            aplicables = aplicables | variante.producto.curvas_disponibles()
        if aplicables.exists():
            self.fields['curva'].queryset = aplicables.distinct()


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'categoria', 'marca', 'genero', 'activo')
    list_editable = ('activo',)
    list_filter = ('categoria', 'genero', 'activo')
    search_fields = ('nombre',)
    prepopulated_fields = {'slug': ('nombre',)}
    inlines = [VarianteInline]

    def get_fields(self, request, obj=None):
        """ El genero solo se muestra en categorias de moda """
        campos = list(super().get_fields(request, obj))
        if obj is not None and not obj.categoria.usa_genero and 'genero' in campos:
            campos.remove('genero')
        return campos


@admin.register(Inventario)
class InventarioAdmin(admin.ModelAdmin):
    """ Pantalla de inventario: stock y costo de cada unidad vendible """
    list_display = ('sku', 'producto', 'color', 'valor', 'stock', 'reservado', 'a_la_venta',
                    'disponibilidad', 'costo_promedio', 'precio_venta', 'margen', 'kardex')
    list_filter = ('variante__producto__categoria', 'variante__producto', 'valor__atributo', 'valor')
    search_fields = ('variante__sku', 'variante__producto__nombre')
    readonly_fields = ('costo_promedio', 'stock')

    @admin.display(description='SKU', ordering='variante__sku')
    def sku(self, obj):
        return obj.variante.sku

    @admin.display(description='Producto', ordering='variante__producto__nombre')
    def producto(self, obj):
        return obj.variante.producto.nombre

    @admin.display(description='Color')
    def color(self, obj):
        return obj.variante.color

    @admin.display(description='A la venta')
    def a_la_venta(self, obj):
        return obj.disponible

    @admin.display(description='Estado')
    def disponibilidad(self, obj):
        libres = obj.disponible
        if libres == 0:
            if obj.reservado:
                return f'TODO RESERVADO ({obj.reservado})'
            return 'AGOTADO'
        if libres <= 2:
            return f'POR AGOTARSE ({libres})'
        return 'DISPONIBLE'

    @admin.display(description='Kardex')
    def kardex(self, obj):
        """ Por que esta unidad tiene ese stock: el historial filtrado por ella """
        url = reverse('admin:web_movimientoinventario_changelist')
        return format_html('<a href="{}?item__id__exact={}">ver historial</a>', url, obj.id)

    @admin.display(description='Margen')
    def margen(self, obj):
        if not obj.costo_promedio:
            return '-'
        ganancia = obj.precio_venta() - obj.costo_promedio
        porcentaje = ganancia / obj.costo_promedio * 100
        return f'S/ {ganancia:.2f} ({porcentaje:.0f}%)'

    def changelist_view(self, request, extra_context=None):
        """ Agrega los totales del inventario (respetando los filtros activos) """
        response = super().changelist_view(request, extra_context)
        try:
            queryset = response.context_data['cl'].queryset
        except (AttributeError, KeyError):
            return response

        decimal = DecimalField(max_digits=12, decimal_places=2)
        totales = queryset.aggregate(
            unidades=Sum('stock'),
            comprometidas=Sum('reservado'),
            valor_costo=Sum(F('stock') * F('costo_promedio'), output_field=decimal),
            valor_venta=Sum(
                F('stock') * Coalesce(F('precio_venta_override'), F('variante__precio_venta')),
                output_field=decimal,
            ),
        )
        response.context_data['total_unidades'] = totales['unidades'] or 0
        response.context_data['total_reservadas'] = totales['comprometidas'] or 0
        response.context_data['total_a_la_venta'] = (
            (totales['unidades'] or 0) - (totales['comprometidas'] or 0)
        )
        response.context_data['total_costo'] = totales['valor_costo'] or 0
        response.context_data['total_venta'] = totales['valor_venta'] or 0
        response.context_data['total_ganancia'] = (totales['valor_venta'] or 0) - (totales['valor_costo'] or 0)
        response.context_data['total_filas'] = queryset.count()
        response.context_data['total_agotados'] = queryset.filter(stock=0).count()
        return response


@admin.register(Campana)
class CampanaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'fecha_inicio', 'fecha_fin', 'porcentaje_descuento', 'activo')
    list_editable = ('activo',)
    filter_horizontal = ('variantes', 'categorias')


@admin.register(Cupon)
class CuponAdmin(admin.ModelAdmin):
    list_display = ('codigo', 'tipo', 'valor', 'fecha_inicio', 'fecha_fin', 'veces_usado', 'activo')
    list_editable = ('activo',)


@admin.register(Proveedor)
class ProveedorAdmin(admin.ModelAdmin):
    list_display = ('razon_social', 'ruc', 'telefono', 'activo')
    list_editable = ('activo',)
    search_fields = ('razon_social', 'ruc')


class MotivoForm(forms.Form):
    """ Deshacer algo exige decir por que: el motivo queda escrito en el kardex """
    motivo = forms.CharField(
        label='Motivo',
        widget=forms.Textarea(attrs={'rows': 3, 'style': 'width:100%;max-width:520px'}),
        help_text='Queda guardado en el documento y en cada movimiento del kardex.',
    )


class CompraDetalleInline(admin.TabularInline):
    model = CompraDetalle
    extra = 1
    readonly_fields = ('subtotal',)
    # buscador en vez de un desplegable con todo el inventario: escribes el SKU y filtra
    autocomplete_fields = ('item',)

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and not obj.editable:
            return ('item', 'cantidad', 'costo_unitario', 'subtotal')
        return super().get_readonly_fields(request, obj)

    def has_add_permission(self, request, obj=None):
        return obj is None or obj.editable

    def has_delete_permission(self, request, obj=None):
        return obj is None or obj.editable


@admin.register(Compra)
class CompraAdmin(admin.ModelAdmin):
    list_display = ('nro_documento', 'tipo_documento', 'proveedor', 'fecha_compra', 'monto_total', 'situacion')
    list_filter = ('tipo_documento', 'aplicado_a_inventario', 'anulado')
    search_fields = ('nro_documento', 'proveedor__razon_social')
    inlines = [CompraDetalleInline]
    actions = ['aplicar_al_inventario', 'anular_compra']

    # lo que nunca se escribe a mano, ni en una boleta en borrador
    CALCULADOS = ('monto_total', 'aplicado_a_inventario', 'anulado', 'fecha_anulacion', 'motivo_anulacion')

    @admin.display(description='Situacion', ordering='aplicado_a_inventario')
    def situacion(self, obj):
        return obj.estado

    def get_readonly_fields(self, request, obj=None):
        """
        Una boleta aplicada es un documento, no un formulario: se congela entera.
        Antes se podia cambiar la cantidad despues de haber sumado el stock, y
        el almacen quedaba diciendo una cosa y la factura otra.
        """
        if obj is not None and not obj.editable:
            return [campo.name for campo in Compra._meta.fields if campo.name != 'id']
        return self.CALCULADOS

    def has_delete_permission(self, request, obj=None):
        # borrar una boleta aplicada dejaba unidades sin ningun documento detras
        return obj is None or obj.editable

    @admin.action(description='Aplicar al inventario (suma stock y recalcula costo)')
    def aplicar_al_inventario(self, request, queryset):
        aplicadas = 0
        for compra in queryset:
            try:
                compra.aplicar_a_inventario(usuario=request.user)
                aplicadas += 1
            except ValueError as error:
                self.message_user(request, f'{compra.nro_documento}: {error}', level='warning')
        if aplicadas:
            self.message_user(request, f'{aplicadas} compra(s) aplicadas al inventario.')

    @admin.action(description='Anular (devuelve el stock que sumo)')
    def anular_compra(self, request, queryset):
        """ Pide el motivo antes de mover nada, porque queda en el kardex """
        if 'aplicar' in request.POST:
            formulario = MotivoForm(request.POST)
            if formulario.is_valid():
                anuladas = 0
                for compra in queryset:
                    try:
                        compra.anular(formulario.cleaned_data['motivo'], usuario=request.user)
                        anuladas += 1
                    except ValueError as error:
                        self.message_user(request, f'{compra.nro_documento}: {error}', level='error')
                if anuladas:
                    self.message_user(request, f'{anuladas} compra(s) anuladas: el stock volvio a su lugar.')
                return None
        else:
            formulario = MotivoForm()

        return render(request, 'admin/confirmar_motivo.html', {
            'titulo': 'Anular compras',
            'explicacion': 'Se descontaran del inventario las unidades que estas boletas sumaron. '
                           'Las boletas no se borran: quedan anuladas, y la entrada y la salida '
                           'siguen visibles en el kardex.',
            'objetos': queryset,
            'formulario': formulario,
            'accion': 'anular_compra',
            'boton': 'Anular',
            'seleccionadas': request.POST.getlist(admin.helpers.ACTION_CHECKBOX_NAME),
        })


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'dni', 'telefono')
    search_fields = ('dni', 'usuario__username', 'usuario__email')


class PedidoDetalleInline(admin.TabularInline):
    model = PedidoDetalle
    extra = 0
    readonly_fields = ('sku', 'nombre_producto', 'valor', 'precio_unitario', 'cantidad', 'subtotal')
    can_delete = False

    def has_add_permission(self, request, obj=None):
        # el detalle es la fotografia de la venta: no se le agregan lineas despues
        return False


@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = ('referencia', 'nombre_comprador', 'apellido_comprador', 'monto_total',
                    'estado', 'entrega', 'es_invitado', 'fecha_registro')
    list_filter = ('estado', 'modo_entrega', 'punto_recojo')
    search_fields = ('nro_pedido', 'codigo_reserva', 'email_comprador',
                     'nombre_comprador', 'apellido_comprador')
    inlines = [PedidoDetalleInline]
    actions = ['marcar_enviado', 'agregar_a_traslado', 'marcar_listo_recojo',
               'marcar_entregado', 'cancelar_pedido']

    # El estado no se escribe a mano: ponerlo en "Cancelado" desde el formulario
    # dejaria el stock descontado para siempre. Se mueve con las acciones.
    readonly_fields = ('codigo_reserva', 'nro_pedido', 'estado', 'monto_total', 'descuento_aplicado',
                       'fecha_cancelacion', 'motivo_cancelacion',
                       'modo_entrega', 'punto_recojo_nombre', 'punto_recojo_direccion')

    @admin.display(description='Entrega', ordering='modo_entrega')
    def entrega(self, obj):
        if obj.es_recojo:
            return f'Recojo · {obj.punto_recojo_nombre}'
        return 'Envio a domicilio'

    def has_delete_permission(self, request, obj=None):
        # un pedido es el respaldo de una venta: se cancela, no se borra
        return False

    def _mover(self, request, queryset, destino):
        movidos = 0
        for pedido in queryset:
            try:
                pedido.cambiar_estado(destino, usuario=request.user)
                movidos += 1
            except ValueError as error:
                self.message_user(request, f'{pedido.referencia}: {error}', level='warning')
        if movidos:
            etiqueta = dict(Pedido.ESTADO_CHOICES)[destino].lower()
            self.message_user(request, f'{movidos} pedido(s) marcados como {etiqueta}.')

    # No hay accion "marcar como pagado": un pedido llega a Pagado validando su
    # comprobante en la pantalla de Pagos, nunca con un cambio de estado suelto.
    # Es la misma regla del resto del proyecto: todo movimiento tiene documento.

    @admin.action(description='Marcar como enviado (pedidos con envio)')
    def marcar_enviado(self, request, queryset):
        self._mover(request, queryset, Pedido.ENVIADO)

    @admin.action(description='Agregar al proximo traslado a su ciudad')
    def agregar_a_traslado(self, request, queryset):
        """
        Sube los pedidos que hay que mandar al viaje ya programado.

        No lo hace el sistema solo: lo decidis vos con la lista delante. Pero si
        el pedido esta esperando y hay camioneta, con un clic queda arriba.
        """
        sumados = 0
        for pedido in queryset.select_related('punto_recojo__ubicacion'):
            destino = getattr(pedido.punto_recojo, 'ubicacion', None)
            if not pedido.es_recojo or destino is None:
                self.message_user(
                    request, f'{pedido.referencia}: no es un pedido de recojo.', level='warning'
                )
                continue

            viaje = (
                Traslado.objects
                .filter(destino=destino, estado=Traslado.PLANIFICADO)
                .order_by('fecha_despacho')
                .first()
            )
            if viaje is None:
                self.message_user(
                    request,
                    f'{pedido.referencia}: no hay transporte programado a {destino}.',
                    level='error',
                )
                continue

            for detalle in pedido.detalles.select_related('item'):
                if detalle.item.ubicacion_id == destino.id:
                    continue      # ya esta en la ciudad, no viaja
                try:
                    viaje.agregar(
                        detalle.item, detalle.cantidad,
                        motivo=TrasladoDetalle.POR_PEDIDO, pedido=pedido,
                    )
                    sumados += 1
                except ValueError as error:
                    self.message_user(request, f'{pedido.referencia}: {error}', level='error')

        if sumados:
            self.message_user(request, f'{sumados} linea(s) agregadas al traslado.')

    @admin.action(description='Marcar como listo para recojo')
    def marcar_listo_recojo(self, request, queryset):
        # aca el cliente recien tiene una fecha real: hasta que el producto no
        # esta en el mostrador no se le promete nada
        self._mover(request, queryset, Pedido.LISTO_RECOJO)

    @admin.action(description='Marcar como entregado')
    def marcar_entregado(self, request, queryset):
        self._mover(request, queryset, Pedido.ENTREGADO)

    @admin.action(description='Cancelar (devuelve el stock al almacen)')
    def cancelar_pedido(self, request, queryset):
        if 'aplicar' in request.POST:
            formulario = MotivoForm(request.POST)
            if formulario.is_valid():
                cancelados = 0
                for pedido in queryset:
                    try:
                        pedido.cancelar(formulario.cleaned_data['motivo'], usuario=request.user)
                        cancelados += 1
                    except ValueError as error:
                        self.message_user(request, f'{pedido.referencia}: {error}', level='error')
                if cancelados:
                    self.message_user(
                        request, f'{cancelados} pedido(s) cancelados: el stock volvio al almacen.'
                    )
                return None
        else:
            formulario = MotivoForm()

        return render(request, 'admin/confirmar_motivo.html', {
            'titulo': 'Cancelar pedidos',
            'explicacion': 'Las unidades vendidas vuelven al inventario y el cupon usado queda '
                           'libre otra vez. El pedido no se borra: queda cancelado, con su detalle '
                           'intacto y su motivo.',
            'objetos': queryset,
            'formulario': formulario,
            'accion': 'cancelar_pedido',
            'boton': 'Cancelar pedidos',
            'seleccionadas': request.POST.getlist(admin.helpers.ACTION_CHECKBOX_NAME),
        })


@admin.register(MovimientoInventario)
class MovimientoInventarioAdmin(admin.ModelAdmin):
    """
    El kardex. Solo se lee: los movimientos los escribe el sistema al aplicar una
    compra, al vender, al anular y al cancelar. Escribir uno a mano seria
    justamente el agujero que esta pantalla viene a cerrar.
    """
    list_display = ('fecha', 'sku', 'unidad', 'movimiento', 'entra', 'sale',
                    'stock_resultante', 'costo_unitario', 'papel', 'usuario')
    list_filter = ('tipo', 'item__variante__producto__categoria', 'fecha')
    search_fields = ('item__variante__sku', 'item__variante__producto__nombre',
                     'compra__nro_documento', 'pedido__nro_pedido',
                     'pedido__codigo_reserva')
    date_hierarchy = 'fecha'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related(
            'item__variante__producto', 'item__variante__color', 'item__valor',
            'compra__proveedor', 'pedido', 'usuario',
        )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    @admin.display(description='SKU', ordering='item__variante__sku')
    def sku(self, obj):
        return obj.item.variante.sku

    @admin.display(description='Unidad')
    def unidad(self, obj):
        return obj.item.etiqueta

    @admin.display(description='Movimiento', ordering='tipo')
    def movimiento(self, obj):
        return obj.get_tipo_display()

    @admin.display(description='Entra')
    def entra(self, obj):
        return obj.cantidad if obj.cantidad > 0 else ''

    @admin.display(description='Sale')
    def sale(self, obj):
        return -obj.cantidad if obj.cantidad < 0 else ''

    @admin.display(description='Documento')
    def papel(self, obj):
        return obj.documento or '-'


@admin.register(CuentaRecaudadora)
class CuentaRecaudadoraAdmin(admin.ModelAdmin):
    """ Las cuentas que ve el cliente en el checkout """
    list_display = ('metodo_corto', 'titular', 'detalle', 'moneda', 'tiene_qr', 'orden', 'activo')
    list_editable = ('orden', 'activo')
    list_filter = ('metodo', 'activo')

    @admin.display(description='Metodo', ordering='metodo')
    def metodo_corto(self, obj):
        return obj.etiqueta

    @admin.display(description='Numero')
    def detalle(self, obj):
        if obj.es_bancaria:
            return obj.numero
        return obj.telefono or '-'

    @admin.display(description='QR', boolean=True)
    def tiene_qr(self, obj):
        return bool(obj.imagen_qr)


@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    """
    La bandeja de comprobantes por revisar. Mientras no exista pasarela, es la
    pantalla mas usada del sistema.
    """
    list_display = ('creado', 'esperando', 'miniatura', 'nro_operacion', 'que_pedido',
                    'cuenta', 'monto_declarado', 'estado', 'descuadre')
    list_filter = ('estado', 'cuenta', 'creado')
    search_fields = ('nro_operacion', 'pedido__nro_pedido', 'pedido__codigo_reserva',
                     'pedido__email_comprador')
    date_hierarchy = 'creado'
    actions = ['validar_pago', 'rechazar_pago']

    # Lo que declaro el cliente es su declaracion: nada se edita desde aca.
    # El voucher entra como `comprobante` y no como el campo crudo: el admin le
    # pide .url a un FileField de solo lectura, y este almacen no tiene URL.
    fields = ('pedido', 'cuenta', 'monto_declarado', 'nro_operacion', 'comprobante',
              'fecha_pago', 'estado', 'monto_confirmado', 'validado_por',
              'fecha_validacion', 'motivo_rechazo', 'creado')
    readonly_fields = fields

    def get_queryset(self, request):
        # los pendientes primero y dentro de ellos el mas viejo arriba: la
        # bandeja se lee de arriba hacia abajo y lo urgente queda a la vista
        return (
            super().get_queryset(request)
            .select_related('pedido', 'cuenta')
            .annotate(_pendiente=Case(
                When(estado=Pago.PENDIENTE, then=0), default=1, output_field=IntegerField(),
            ))
            .order_by('_pendiente', 'creado')
        )

    def has_add_permission(self, request):
        # el comprobante lo declara el cliente desde la tienda
        return False

    def has_delete_permission(self, request, obj=None):
        # un comprobante rechazado sigue siendo parte de la historia del pedido
        return False

    @admin.display(description='Pedido', ordering='pedido__nro_pedido')
    def que_pedido(self, obj):
        return obj.pedido.referencia

    @admin.display(description='Esperando', ordering='creado')
    def esperando(self, obj):
        if obj.estado != Pago.PENDIENTE:
            return '-'
        if obj.demorado:
            return format_html('<b style="color:#b5541b">{}</b>', obj.espera)
        return obj.espera

    @admin.display(description='')
    def miniatura(self, obj):
        """ El voucher en la lista: menos clics por pago """
        if not obj.voucher:
            return '-'
        url = reverse('web:voucherPago', args=[obj.id])
        return format_html(
            '<a href="{}" target="_blank">'
            '<img src="{}" style="height:52px;border:1px solid #ddd;border-radius:3px">'
            '</a>', url, url,
        )

    @admin.display(description='Diferencia')
    def descuadre(self, obj):
        if obj.diferencia is None:
            return '-'
        if obj.diferencia == 0:
            return 'cuadra'
        return f'{obj.diferencia:+.2f}'

    @admin.display(description='Comprobante')
    def comprobante(self, obj):
        """ La imagen, servida por la vista con permisos y no por /media/ """
        if not obj.voucher:
            return '-'
        url = reverse('web:voucherPago', args=[obj.id])
        return format_html(
            '<a href="{}" target="_blank">'
            '<img src="{}" style="max-width:340px;border:1px solid #ddd;border-radius:4px">'
            '</a>', url, url,
        )

    @admin.action(description='Validar (pide el monto que viste en tu cuenta)')
    def validar_pago(self, request, queryset):
        if 'aplicar' in request.POST:
            formulario = ValidacionForm(request.POST)
            if formulario.is_valid():
                monto = formulario.cleaned_data['monto_confirmado']
                validados = 0
                for pago in queryset:
                    try:
                        pago.validar(monto, usuario=request.user)
                        validados += 1
                        if not pago.cuadra:
                            self.message_user(
                                request,
                                f'{pago.nro_operacion}: quedo con una diferencia de '
                                f'{pago.diferencia:+.2f} contra el total del pedido.',
                                level='warning',
                            )
                    except ValueError as error:
                        self.message_user(request, f'{pago.nro_operacion}: {error}', level='error')
                if validados:
                    self.message_user(request, f'{validados} pago(s) validados.')
                return None
        else:
            formulario = ValidacionForm()

        return render(request, 'admin/validar_pago.html', {
            'pagos': queryset.select_related('pedido', 'cuenta'),
            'formulario': formulario,
            'accion': 'validar_pago',
            'seleccionadas': request.POST.getlist(admin.helpers.ACTION_CHECKBOX_NAME),
        })

    @admin.action(description='Rechazar (el cliente vera el motivo)')
    def rechazar_pago(self, request, queryset):
        if 'aplicar' in request.POST:
            formulario = MotivoForm(request.POST)
            if formulario.is_valid():
                rechazados = 0
                for pago in queryset:
                    try:
                        pago.rechazar(formulario.cleaned_data['motivo'], usuario=request.user)
                        rechazados += 1
                    except ValueError as error:
                        self.message_user(request, f'{pago.nro_operacion}: {error}', level='error')
                if rechazados:
                    self.message_user(
                        request,
                        f'{rechazados} comprobante(s) rechazados. El cliente puede enviar otro.'
                    )
                return None
        else:
            formulario = MotivoForm()

        return render(request, 'admin/confirmar_motivo.html', {
            'titulo': 'Rechazar comprobantes',
            'explicacion': 'El pedido sigue esperando pago y el cliente va a leer este motivo, '
                           'asi que escribilo pensando en el.',
            'objetos': queryset,
            'formulario': formulario,
            'accion': 'rechazar_pago',
            'boton': 'Rechazar',
            'seleccionadas': request.POST.getlist(admin.helpers.ACTION_CHECKBOX_NAME),
        })


@admin.register(PuntoRecojo)
class PuntoRecojoAdmin(admin.ModelAdmin):
    """ Los mostradores donde el cliente retira """
    list_display = ('nombre', 'direccion', 'distrito', 'provincia', 'horario', 'orden', 'activo')
    list_editable = ('orden', 'activo')
    list_filter = ('provincia', 'activo')
    search_fields = ('nombre', 'direccion')


@admin.register(Ubicacion)
class UbicacionAdmin(admin.ModelAdmin):
    """ Las ciudades donde vive el stock """
    list_display = ('nombre', 'es_principal', 'cuando_sale', 'dias_viaje', 'unidades', 'activo')
    list_editable = ('activo',)

    @admin.display(description='Sale', ordering='dia_despacho')
    def cuando_sale(self, obj):
        return obj.get_dia_despacho_display()

    @admin.display(description='Unidades')
    def unidades(self, obj):
        return obj.items.aggregate(t=Sum('stock'))['t'] or 0


class TrasladoDetalleInline(admin.TabularInline):
    model = TrasladoDetalle
    extra = 0
    fields = ('item', 'cantidad', 'motivo', 'pedido', 'cantidad_recibida')
    readonly_fields = ('item_destino', 'costo_unitario')
    autocomplete_fields = ('item',)

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and not obj.editable:
            return ('item', 'cantidad', 'motivo', 'pedido', 'cantidad_recibida',
                    'item_destino', 'costo_unitario')
        return super().get_readonly_fields(request, obj)

    def has_add_permission(self, request, obj=None):
        return obj is None or obj.editable

    def has_delete_permission(self, request, obj=None):
        return obj is None or obj.editable


@admin.register(Traslado)
class TrasladoAdmin(admin.ModelAdmin):
    """
    El viaje. Nace vacio y se llena solo con lo que de verdad va a subir a la
    camioneta: los pedidos que ya se vendieron, y lo que decidas mandar por
    stock, idealmente cerca de la fecha de salida.
    """
    list_display = ('__str__', 'fecha_despacho', 'fecha_disponible', 'estado',
                    'cuantas_unidades', 'por_pedido', 'faltantes')
    list_filter = ('estado', 'destino')
    date_hierarchy = 'fecha_despacho'
    inlines = [TrasladoDetalleInline]
    actions = ['despachar_traslado', 'recibir_traslado', 'anular_traslado']

    CALCULADOS = ('estado', 'despachado_por', 'fecha_salida', 'recibido_por',
                  'fecha_recepcion', 'motivo_anulacion', 'creado')

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and not obj.editable:
            return [c.name for c in Traslado._meta.fields if c.name != 'id']
        return self.CALCULADOS

    def has_delete_permission(self, request, obj=None):
        # un viaje que ya movio mercaderia no se borra: se anula si no salio
        return obj is None or obj.editable

    @admin.display(description='Unidades')
    def cuantas_unidades(self, obj):
        return obj.unidades

    @admin.display(description='Por pedido')
    def por_pedido(self, obj):
        return obj.detalles.filter(motivo=TrasladoDetalle.POR_PEDIDO).count()

    @admin.display(description='Faltantes')
    def faltantes(self, obj):
        perdidas = sum(
            d.faltante or 0 for d in obj.detalles.all() if d.cantidad_recibida is not None
        )
        if not perdidas:
            return '-'
        return format_html('<b style="color:#b5541b">{}</b>', perdidas)

    @admin.action(description='Despachar (la mercaderia sale)')
    def despachar_traslado(self, request, queryset):
        salieron = 0
        for viaje in queryset:
            try:
                viaje.despachar(usuario=request.user)
                salieron += 1
            except ValueError as error:
                self.message_user(request, f'{viaje}: {error}', level='warning')
        if salieron:
            self.message_user(request, f'{salieron} traslado(s) en camino.')

    @admin.action(description='Recibir contando lo que llego')
    def recibir_traslado(self, request, queryset):
        """
        Quien recibe cuenta, no acepta. Mismo criterio que la boveda con una
        remesa: si los numeros no coinciden, la diferencia queda escrita.
        """
        viajes = queryset.filter(estado=Traslado.EN_TRANSITO)

        if 'aplicar' in request.POST:
            recibidos = 0
            for viaje in viajes:
                conteo = {}
                for detalle in viaje.detalles.all():
                    campo = request.POST.get(f'llegaron-{detalle.id}')
                    conteo[detalle.id] = int(campo) if campo not in (None, '') else 0
                try:
                    viaje.recibir(usuario=request.user, conteo=conteo)
                    recibidos += 1
                    perdidas = sum(d.faltante or 0 for d in viaje.detalles.all())
                    if perdidas:
                        self.message_user(
                            request,
                            f'{viaje}: faltaron {perdidas} unidad(es) contra lo declarado.',
                            level='warning',
                        )
                except ValueError as error:
                    self.message_user(request, f'{viaje}: {error}', level='error')
            if recibidos:
                self.message_user(request, f'{recibidos} traslado(s) recibidos.')
            return None

        return render(request, 'admin/recibir_traslado.html', {
            'viajes': viajes.prefetch_related('detalles__item'),
            'accion': 'recibir_traslado',
            'seleccionadas': request.POST.getlist(admin.helpers.ACTION_CHECKBOX_NAME),
        })

    @admin.action(description='Anular (devuelve las unidades a la venta)')
    def anular_traslado(self, request, queryset):
        if 'aplicar' in request.POST:
            formulario = MotivoForm(request.POST)
            if formulario.is_valid():
                anulados = 0
                for viaje in queryset:
                    try:
                        viaje.anular(formulario.cleaned_data['motivo'], usuario=request.user)
                        anulados += 1
                    except ValueError as error:
                        self.message_user(request, f'{viaje}: {error}', level='error')
                if anulados:
                    self.message_user(request, f'{anulados} traslado(s) anulados.')
                return None
        else:
            formulario = MotivoForm()

        return render(request, 'admin/confirmar_motivo.html', {
            'titulo': 'Anular traslados',
            'explicacion': 'Las unidades que estaban comprometidas vuelven a estar a la '
                           'venta en su ciudad. Solo se anula lo que todavia no salio.',
            'objetos': queryset,
            'formulario': formulario,
            'accion': 'anular_traslado',
            'boton': 'Anular',
            'seleccionadas': request.POST.getlist(admin.helpers.ACTION_CHECKBOX_NAME),
        })
