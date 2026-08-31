from uuid import uuid4

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from .avisos import enlace_whatsapp as _whatsapp
from .carrito import Cart
from .disponibilidad import para_carrito
from .forms import ClienteForm, PagoForm, PedidoForm, RechazoForm, ValidacionForm
from .models import (
    Categoria, Cliente, CuentaRecaudadora, Inventario, Pago, Pedido, PuntoRecojo,
    Traslado, Ubicacion, Variante,
)
from .pedidos import PedidoError, crear_pedido

""" VISTAS PARA EL CATALOGO DE PRODUCTOS """


def _variantes_visibles():
    """ Solo colores activos de productos activos, con producto y categoria ya cargados """
    return (
        Variante.objects
        .filter(activo=True, producto__activo=True)
        .select_related('producto', 'producto__categoria')
    )


def _contexto_catalogo(variantes, **extra):
    context = {
        'variantes': variantes,
        'categorias': Categoria.objects.filter(activo=True),
    }
    context.update(extra)
    return context


def index(request):
    return render(request, 'index.html', _contexto_catalogo(_variantes_visibles()))


def productosPorCategoria(request, categoria_id):
    """ vista para filtrar productos por categoria """
    categoria = get_object_or_404(Categoria, pk=categoria_id)
    variantes = _variantes_visibles().filter(producto__categoria=categoria)
    return render(request, 'index.html', _contexto_catalogo(variantes, categoria_actual=categoria))


def productosPorNombre(request):
    """ vista para filtrado de productos por nombre """
    nombre = request.POST.get('nombre', '').strip()
    variantes = _variantes_visibles()
    if nombre:
        variantes = variantes.filter(producto__nombre__icontains=nombre)
    return render(request, 'index.html', _contexto_catalogo(variantes, busqueda=nombre))


def productoDetalle(request, sku):
    """ Detalle de un color: sus unidades vendibles y los otros colores del mismo producto """
    variante = get_object_or_404(_variantes_visibles(), sku=sku)
    items = Inventario.precargar_vencidas(
        variante.items.select_related('valor__atributo').order_by('valor__orden', 'valor__valor')
    )
    otros_colores = _variantes_visibles().filter(producto=variante.producto).exclude(pk=variante.pk)

    atributo = variante.producto.categoria.atributo

    context = {
        'variante': variante,
        'producto': variante.producto,
        'items': items,
        'atributo': atributo,
        'usa_atributo': atributo is not None,
        'otros_colores': otros_colores,
        'hay_stock': any(i.disponible for i in items),
    }
    return render(request, 'producto.html', context)


"""" VISTAS PARA EL CARRITO DE COMPRAS """


def carrito(request):
    carrito_actual = Cart(request)
    fallas = carrito_actual.problemas()
    return render(request, 'carrito.html', {
        'carrito': carrito_actual,
        # cada linea con su motivo pegado: la plantilla no sabe buscar en un
        # diccionario por clave sin un templatetag, y no vale uno para esto
        'lineas': [dict(l, falla=fallas.get(l['item_id'], '')) for l in carrito_actual],
        'hay_fallas': bool(fallas),
        # el que se quedo sin producto necesita a quien reclamarle, sobre todo
        # si ya habia transferido: desde aca ya no llega a /pago
        'whatsapp': _whatsapp(),
    })


def _es_ajax(request):
    return request.headers.get('X-Requested-With') == 'XMLHttpRequest'


def agregarCarrito(request):
    """
    La unidad elegida (talla, capacidad...) llega en el POST, porque el cliente
    la selecciona en el formulario. Si la peticion viene por AJAX responde JSON y
    el cliente se queda en la pagina; si no, redirige al producto (sin JavaScript).
    """
    if request.method != 'POST':
        return redirect('web:carrito')

    sku = request.POST.get('sku')
    item_id = request.POST.get('item_id')

    def error(mensaje):
        if _es_ajax(request):
            return JsonResponse({'ok': False, 'mensaje': mensaje}, status=400)
        messages.error(request, mensaje)
        return redirect('web:producto', sku=sku) if sku else redirect('web:index')

    if not item_id:
        return error('Elige una opcion antes de agregar al carrito')

    item = get_object_or_404(
        Inventario.objects.select_related('variante__producto__categoria', 'valor__atributo'),
        pk=item_id,
    )

    try:
        cantidad = int(request.POST.get('cantidad', 1))
    except (TypeError, ValueError):
        cantidad = 1

    carrito_actual = Cart(request)
    try:
        carrito_actual.add(item, cantidad)
    except ValueError as problema:
        return error(str(problema))

    variante = item.variante
    detalle = f' {item.valor.atributo.nombre.lower()} {item.valor}' if item.valor_id else ''
    mensaje = f'{variante.producto.nombre} ({variante.color}){detalle} agregado al carrito'

    if _es_ajax(request):
        return JsonResponse({
            'ok': True,
            'mensaje': mensaje,
            'items': carrito_actual.cantidad_items,
            'lineas': len(carrito_actual),
            'total': str(carrito_actual.total),
        })

    messages.success(request, mensaje)
    return redirect('web:producto', sku=variante.sku)


def actualizarCarrito(request, item_id):
    if request.method != 'POST':
        return redirect('web:carrito')

    item = get_object_or_404(Inventario.objects.select_related('valor__atributo'), pk=item_id)
    try:
        cantidad = int(request.POST.get('cantidad', 1))
    except (TypeError, ValueError):
        cantidad = 1

    try:
        Cart(request).actualizar(item, cantidad)
    except ValueError as error:
        messages.error(request, str(error))

    return redirect('web:carrito')


def eliminarProductoCarrito(request, item_id):
    item = get_object_or_404(Inventario, pk=item_id)
    Cart(request).delete(item)
    return redirect('web:carrito')


def limpiarCarrito(request):
    Cart(request).clear()
    return redirect('web:carrito')


def aplicarCupon(request):
    if request.method != 'POST':
        return redirect('web:carrito')

    try:
        cupon = Cart(request).aplicar_cupon(request.POST.get('codigo'))
    except ValueError as problema:
        messages.error(request, str(problema))
    else:
        messages.success(request, f'Cupon {cupon.codigo} aplicado')

    return redirect('web:carrito')


def quitarCupon(request):
    Cart(request).quitar_cupon()
    messages.success(request, 'Cupon retirado')
    return redirect('web:carrito')


""" VISTAS PARA CLIENTES Y USUARIOS """


def crearUsuario(request):

    if request.method == 'POST':
        dataUsuario = request.POST['nuevoUsuario']
        dataPassword = request.POST['nuevoPassword']

        if User.objects.filter(username=dataUsuario).exists():
            return render(request, 'login.html', {'mensajeError': 'Ese usuario ya esta registrado'})

        nuevoUsuario = User.objects.create_user(username=dataUsuario, password=dataPassword)
        if nuevoUsuario is not None:
            login(request, nuevoUsuario)
            return redirect('/cuenta')

    return render(request, 'login.html')


def loginUsuario(request):
    paginaDestino = request.GET.get('next', None)
    context = {
        'destino': paginaDestino
    }

    if request.method == 'POST':
        dataUsuario = request.POST['usuario']
        dataPassword = request.POST['password']
        dataDestino = request.POST['destino']

        usuarioAuth = authenticate(request, username=dataUsuario, password=dataPassword)
        if usuarioAuth is not None:
            login(request, usuarioAuth)

            if dataDestino != 'None':
                return redirect(dataDestino)

            return redirect('/cuenta')
        else:
            context = {
                'mensajeError': 'Datos Incorrectos'
            }

    return render(request, 'login.html', context)


def logoutUsuario(request):
    logout(request)
    return redirect('web:index')


def _datos_cliente(usuario):
    """ Arma el diccionario inicial del formulario con lo que ya tenga el cliente """
    datos = {
        'nombre': usuario.first_name,
        'apellidos': usuario.last_name,
        'email': usuario.email,
    }
    cliente = Cliente.objects.filter(usuario=usuario).first()
    if cliente:
        datos.update({
            'direccion': cliente.direccion,
            'telefono': cliente.telefono,
            'dni': cliente.dni,
            'sexo': cliente.sexo,
            'fecha_nacimiento': cliente.fecha_nacimiento,
        })
    return datos


@login_required(login_url='/login')
def cuentaUsuario(request):
    return render(request, 'cuenta.html', {'frmCliente': ClienteForm(_datos_cliente(request.user))})


@login_required(login_url='/login')
def actualizarCliente(request):
    mensaje = ''
    frmCliente = ClienteForm(_datos_cliente(request.user))

    if request.method == 'POST':
        frmCliente = ClienteForm(request.POST)
        if frmCliente.is_valid():
            dataCliente = frmCliente.cleaned_data

            # actualizar usuario
            actUsuario = request.user
            actUsuario.first_name = dataCliente['nombre']
            actUsuario.last_name = dataCliente['apellidos']
            actUsuario.email = dataCliente['email']
            actUsuario.save()

            # crear o actualizar al cliente (nunca duplicarlo)
            Cliente.objects.update_or_create(
                usuario=actUsuario,
                defaults={
                    'dni': dataCliente['dni'],
                    'direccion': dataCliente['direccion'],
                    'telefono': dataCliente['telefono'],
                    'sexo': dataCliente['sexo'],
                    'fecha_nacimiento': dataCliente['fecha_nacimiento'],
                },
            )

            mensaje = 'Datos Actualizados'

    context = {
        'mensaje': mensaje,
        'frmCliente': frmCliente,
    }

    return render(request, 'cuenta.html', context)


"""" VISTAS PARA PROCESO DE COMPRA """


def _datos_pedido(usuario):
    """ Precarga el checkout con los datos del cliente si ya inicio sesion """
    if not usuario.is_authenticated:
        return {}

    datos = {
        'nombre': usuario.first_name,
        'apellidos': usuario.last_name,
        'email': usuario.email,
    }
    cliente = Cliente.objects.filter(usuario=usuario).first()
    if cliente:
        datos.update({
            'telefono': cliente.telefono,
            'dni': cliente.dni,
            'direccion': cliente.direccion,
        })
    return datos


CLAVE_INVITADO = 'compra_como_invitado'


def identificarse(request):
    """ Antes del checkout: elegir entre iniciar sesion o comprar como invitado """
    if len(Cart(request)) == 0:
        messages.error(request, 'Tu carrito esta vacio')
        return redirect('web:carrito')

    if request.user.is_authenticated:
        return redirect('web:registrarPedido')

    return render(request, 'identificacion.html')


def continuarComoInvitado(request):
    request.session[CLAVE_INVITADO] = True
    return redirect('web:registrarPedido')


def registrarPedido(request):
    """ Checkout. No exige cuenta: tambien se puede comprar como invitado. """
    carrito_actual = Cart(request)
    if len(carrito_actual) == 0:
        messages.error(request, 'Tu carrito esta vacio')
        return redirect('web:carrito')

    # sin sesion iniciada y sin haber elegido "invitado", primero se identifica
    if not request.user.is_authenticated and not request.session.get(CLAVE_INVITADO):
        return redirect('web:identificarse')

    puntos = PuntoRecojo.objects.filter(activo=True)

    if request.method == 'POST':
        frmPedido = PedidoForm(request.POST, puntos=puntos)
        if frmPedido.is_valid():
            try:
                pedido = crear_pedido(carrito_actual, frmPedido.cleaned_data, request.user)
            except PedidoError as problema:
                messages.error(request, str(problema))
                return redirect('web:carrito')

            carrito_actual.clear()
            request.session.pop(CLAVE_INVITADO, None)
            request.session['ultimo_pedido'] = pedido.id
            return redirect('web:pagoPedido')

        messages.error(request, 'Revisa los datos marcados en el formulario')
    else:
        # un token por formulario pintado: identifica este intento de compra,
        # y vuelve igual por mas veces que el cliente apriete Confirmar
        inicial = dict(_datos_pedido(request.user), token_checkout=uuid4().hex)
        frmPedido = PedidoForm(initial=inicial, puntos=puntos)

    # que se le puede prometer en cada mostrador, con el carrito que trae
    lineas = [
        (item, next(l['cantidad'] for l in carrito_actual if l['item_id'] == item.id))
        for item in Inventario.objects.filter(
            id__in=[l['item_id'] for l in carrito_actual]
        ).select_related('variante__producto__categoria', 'ubicacion')
    ]
    ofertas = []
    for punto in puntos.select_related('ubicacion'):
        clave, fecha = para_carrito(lineas, punto)
        ofertas.append({'punto': punto, 'clave': clave, 'fecha': fecha})

    context = {
        'frmPedido': frmPedido,
        'carrito': carrito_actual,
        'puntos': puntos,
        'ofertas': ofertas,
    }
    return render(request, 'pedido.html', context)


def pagoPedido(request):
    """
    Donde el cliente ve nuestras cuentas y declara su pago.

    Lee el pedido de la sesion, igual que gracias(), para no exponerlo en la URL.
    Nada de lo que llega aca se da por cobrado: el pedido pasa a "En validacion"
    y alguien lo confirma despues mirando la cuenta real.
    """
    pedido_id = request.session.get('ultimo_pedido')
    if not pedido_id:
        return redirect('web:index')

    pedido = get_object_or_404(
        Pedido.objects.prefetch_related('detalles', 'pagos'), pk=pedido_id
    )
    if pedido.descontado:
        return redirect('web:gracias')

    # si el reloj se paso, se cierra el pedido, las unidades vuelven a la venta
    # y el carrito se rearma con lo que tenia: el cliente no perdio su seleccion,
    # perdio la reserva. Volver a un carrito vacio, sin explicacion, es la peor
    # forma de enterarse.
    # dos caminos al mismo lugar: o el plazo se acaba de cumplir, o el barrido
    # llego primero y el pedido ya figura expirado. Antes solo se miraba lo
    # primero, y como el barrido corre cada minuto, casi nunca se cumplia
    if pedido.reserva_vencida or pedido.cerrado_sin_venta:
        detalles = list(pedido.detalles.select_related('item'))
        if pedido.reserva_vencida:
            pedido.expirar()
        pedido.refresh_from_db()

        Cart(request).restaurar([(d.item, d.cantidad) for d in detalles])
        request.session.pop('ultimo_pedido', None)

        if pedido.estado == Pedido.EXPIRADO:
            aviso = 'Se vencio el plazo y soltamos tus productos.'
        else:
            aviso = f'No pudimos confirmar tu pago: {pedido.motivo_cancelacion}'
        messages.error(
            request,
            f'{aviso} Los devolvimos a tu carrito: si siguen disponibles podes '
            'volver a confirmar el pedido.',
        )
        return redirect('web:carrito')

    cuentas = CuentaRecaudadora.objects.filter(activo=True)
    if not cuentas.exists():
        messages.error(request, 'No hay cuentas de pago configuradas. Escribenos para coordinar.')
        return redirect('web:gracias')

    pendiente = pedido.pago_pendiente

    if request.method == 'POST' and pendiente is None:
        frmPago = PagoForm(request.POST, request.FILES, cuentas=cuentas)
        if frmPago.is_valid():
            datos = frmPago.cleaned_data
            pedido.declarar_pago(
                cuenta=datos['cuenta'],
                monto_declarado=datos['monto_declarado'],
                nro_operacion=datos['nro_operacion'],
                voucher=datos['voucher'],
                fecha_pago=datos['fecha_pago'],
                base_url=request.build_absolute_uri('/').rstrip('/'),
            )
            messages.success(request, 'Recibimos tu comprobante. Lo estamos revisando.')
            return redirect('web:gracias')

        messages.error(request, 'Revisa los datos marcados del comprobante')
    else:
        frmPago = PagoForm(
            cuentas=cuentas,
            initial={
                'monto_declarado': pedido.monto_total,
                'fecha_pago': timezone.localdate(),
                'cuenta': cuentas.first(),
            },
        )

    return render(request, 'pago.html', {
        'pedido': pedido,
        'cuentas': cuentas,
        'frmPago': frmPago,
        'pendiente': pendiente,
        'segundos': pedido.segundos_restantes if pedido.estado == Pedido.SOLICITADO else None,
        'whatsapp': _whatsapp(pedido),
    })


@staff_member_required
def transportes(request):
    """
    El estado del transporte entre ciudades.

    Existe por el punto ciego del modulo: sin viaje programado una ciudad deja de
    ofrecer productos y nadie reclama, porque el cliente no ve un error, ve un
    catalogo mas chico. Aca se ve de un vistazo, y sirve igual con dos ciudades
    que con cinco.
    """
    ciudades = []
    for ubicacion in Ubicacion.objects.filter(activo=True).exclude(es_principal=True):
        viaje = ubicacion.proximo_traslado()
        ciudades.append({
            'ubicacion': ubicacion,
            'viaje': viaje,
            'sin_viaje': viaje is None,
            'esperando': Traslado.pedidos_esperando(destino=ubicacion),
            'unidades': sum(i.stock for i in ubicacion.items.all()),
            'puntos': ubicacion.puntos.filter(activo=True).count(),
        })

    return render(request, 'transportes.html', {
        'ciudades': ciudades,
        'sin_transporte': [c for c in ciudades if c['sin_viaje']],
    })


@staff_member_required
def validarPagos(request):
    """
    La cola de comprobantes por revisar, hecha para el celular.

    Existe por una razon concreta: la notificacion de Yape llega al telefono, y
    si validar exige sentarse frente a una computadora el pago se confirma horas
    despues. Aca se confirma donde uno esta.
    """
    pendientes = (
        Pago.objects
        .filter(estado=Pago.PENDIENTE)
        .select_related('pedido', 'cuenta')
        .order_by('creado')
    )
    return render(request, 'validar.html', {
        'pendientes': pendientes,
        'horas_promesa': settings.HORAS_VALIDACION,
    })


@staff_member_required
def validarPago(request, pago_id):
    """
    Un comprobante: el voucher grande, los datos al lado, y dos botones.

    Validar sigue exigiendo escribir el monto que se vio en la cuenta. Esa es la
    friccion que importa y no se saca; la que se saca es la de navegar.
    """
    pago = get_object_or_404(
        Pago.objects.select_related('pedido', 'cuenta'), pk=pago_id
    )

    frmValidar, frmRechazar = ValidacionForm(), RechazoForm()

    if request.method == 'POST' and pago.estado == Pago.PENDIENTE:
        if 'validar' in request.POST:
            frmValidar = ValidacionForm(request.POST)
            if frmValidar.is_valid():
                try:
                    pago.validar(frmValidar.cleaned_data['monto_confirmado'], usuario=request.user)
                except ValueError as problema:
                    messages.error(request, str(problema))
                else:
                    if pago.cuadra:
                        messages.success(request, f'{pago.pedido.referencia} confirmado.')
                    else:
                        messages.error(
                            request,
                            f'{pago.pedido.referencia} quedo con una diferencia de '
                            f'{pago.diferencia:+.2f} contra el total del pedido.'
                        )
                    return redirect('web:validarPagos')

        elif 'rechazar' in request.POST:
            frmRechazar = RechazoForm(request.POST)
            if frmRechazar.is_valid():
                try:
                    pago.rechazar(frmRechazar.cleaned_data['motivo'], usuario=request.user)
                except ValueError as problema:
                    messages.error(request, str(problema))
                else:
                    messages.success(request, 'Comprobante rechazado. El cliente puede enviar otro.')
                    return redirect('web:validarPagos')

    return render(request, 'validar_detalle.html', {
        'pago': pago,
        'pedido': pago.pedido,
        'frmValidar': frmValidar,
        'frmRechazar': frmRechazar,
    })


def voucherPago(request, pago_id):
    """
    Entrega la imagen del comprobante.

    No puede vivir suelta en /media/: un voucher es el pantallazo bancario del
    cliente, con su nombre y sus montos. Cualquiera con la URL lo leeria. Se
    entrega solo al staff y a quien hizo ese pedido, y si no corresponde se
    responde 404 y no 403, para no confirmar siquiera que existe.
    """
    pago = get_object_or_404(Pago.objects.select_related('pedido__cliente'), pk=pago_id)
    pedido = pago.pedido

    es_duenno = (
        request.user.is_staff
        or (pedido.cliente_id and pedido.cliente.usuario_id == request.user.id)
        or request.session.get('ultimo_pedido') == pedido.id
    )
    if not es_duenno or not pago.voucher:
        raise Http404

    return FileResponse(pago.voucher.open('rb'), content_type='image/*')


def gracias(request):
    """ Confirmacion. Lee el pedido de la sesion, no de la URL, para no exponerlo. """
    pedido_id = request.session.get('ultimo_pedido')
    if not pedido_id:
        return redirect('web:index')

    pedido = get_object_or_404(Pedido.objects.prefetch_related('detalles'), pk=pedido_id)
    return render(request, 'gracias.html', {
        'pedido': pedido,
        'whatsapp': _whatsapp(pedido),
    })
