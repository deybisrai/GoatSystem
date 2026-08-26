from django import forms
from django.contrib import admin
from django.db.models import DecimalField, F, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import render
from django.utils.html import format_html

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
    Inventario,
    Pedido,
    PedidoDetalle,
    Producto,
    Proveedor,
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
    list_display = ('sku', 'producto', 'color', 'valor', 'stock', 'disponibilidad', 'costo_promedio', 'precio_venta', 'margen')
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

    @admin.display(description='Estado')
    def disponibilidad(self, obj):
        if obj.stock == 0:
            return 'AGOTADO'
        if obj.stock <= 2:
            return f'POR AGOTARSE ({obj.stock})'
        return 'DISPONIBLE'

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
            valor_costo=Sum(F('stock') * F('costo_promedio'), output_field=decimal),
            valor_venta=Sum(
                F('stock') * Coalesce(F('precio_venta_override'), F('variante__precio_venta')),
                output_field=decimal,
            ),
        )
        response.context_data['total_unidades'] = totales['unidades'] or 0
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


class CompraDetalleInline(admin.TabularInline):
    model = CompraDetalle
    extra = 1
    readonly_fields = ('subtotal',)
    # buscador en vez de un desplegable con todo el inventario: escribes el SKU y filtra
    autocomplete_fields = ('item',)


@admin.register(Compra)
class CompraAdmin(admin.ModelAdmin):
    list_display = ('nro_documento', 'tipo_documento', 'proveedor', 'fecha_compra', 'monto_total', 'aplicado_a_inventario')
    list_filter = ('tipo_documento', 'aplicado_a_inventario')
    search_fields = ('nro_documento', 'proveedor__razon_social')
    readonly_fields = ('monto_total', 'aplicado_a_inventario')
    inlines = [CompraDetalleInline]
    actions = ['aplicar_al_inventario']

    @admin.action(description='Aplicar al inventario (suma stock y recalcula costo)')
    def aplicar_al_inventario(self, request, queryset):
        aplicadas = 0
        for compra in queryset:
            compra.recalcular_total()
            try:
                compra.aplicar_a_inventario()
                aplicadas += 1
            except ValueError as error:
                self.message_user(request, f'{compra.nro_documento}: {error}', level='warning')
        if aplicadas:
            self.message_user(request, f'{aplicadas} compra(s) aplicadas al inventario.')


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ('usuario', 'dni', 'telefono')
    search_fields = ('dni', 'usuario__username', 'usuario__email')


class PedidoDetalleInline(admin.TabularInline):
    model = PedidoDetalle
    extra = 0
    readonly_fields = ('sku', 'nombre_producto', 'valor', 'precio_unitario', 'cantidad', 'subtotal')
    can_delete = False


@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = ('nro_pedido', 'nombre_comprador', 'apellido_comprador', 'monto_total', 'estado', 'es_invitado', 'fecha_registro')
    list_filter = ('estado',)
    search_fields = ('nro_pedido', 'email_comprador', 'nombre_comprador', 'apellido_comprador')
    inlines = [PedidoDetalleInline]
