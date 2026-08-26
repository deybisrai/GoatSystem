from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render

from .carrito import Cart
from .forms import ClienteForm, PedidoForm
from .models import Categoria, Cliente, Pedido, Variante, Inventario
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
    items = variante.items.select_related('valor__atributo').order_by('valor__orden', 'valor__valor')
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
    return render(request, 'carrito.html', {'carrito': Cart(request)})


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

    if request.method == 'POST':
        frmPedido = PedidoForm(request.POST)
        if frmPedido.is_valid():
            try:
                pedido = crear_pedido(carrito_actual, frmPedido.cleaned_data, request.user)
            except PedidoError as problema:
                messages.error(request, str(problema))
                return redirect('web:carrito')

            carrito_actual.clear()
            request.session.pop(CLAVE_INVITADO, None)
            request.session['ultimo_pedido'] = pedido.id
            return redirect('web:gracias')

        messages.error(request, 'Revisa los datos marcados en el formulario')
    else:
        frmPedido = PedidoForm(initial=_datos_pedido(request.user))

    context = {
        'frmPedido': frmPedido,
        'carrito': carrito_actual,
    }
    return render(request, 'pedido.html', context)


def gracias(request):
    """ Confirmacion. Lee el pedido de la sesion, no de la URL, para no exponerlo. """
    pedido_id = request.session.get('ultimo_pedido')
    if not pedido_id:
        return redirect('web:index')

    pedido = get_object_or_404(Pedido.objects.prefetch_related('detalles'), pk=pedido_id)
    return render(request, 'gracias.html', {'pedido': pedido})
