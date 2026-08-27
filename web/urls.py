from django.urls import path

from . import views

app_name = 'web'

urlpatterns = [
    path('', views.index, name='index'),
    path('productosPorCategoria/<int:categoria_id>', views.productosPorCategoria, name='productosPorCategoria'),
    path('productosPorNombre', views.productosPorNombre, name='productosPorNombre'),
    path('producto/<str:sku>', views.productoDetalle, name='producto'),

    path('carrito', views.carrito, name='carrito'),
    path('agregarCarrito', views.agregarCarrito, name='agregarCarrito'),
    path('actualizarCarrito/<int:item_id>', views.actualizarCarrito, name='actualizarCarrito'),
    path('eliminarProductoCarrito/<int:item_id>', views.eliminarProductoCarrito, name='eliminarProductoCarrito'),
    path('limpiarCarrito', views.limpiarCarrito, name='limpiarCarrito'),
    path('aplicarCupon', views.aplicarCupon, name='aplicarCupon'),
    path('quitarCupon', views.quitarCupon, name='quitarCupon'),

    path('crearUsuario', views.crearUsuario, name='crearUsuario'),
    path('cuenta', views.cuentaUsuario, name='cuentaUsuario'),
    path('actualizarCliente', views.actualizarCliente, name='actualizarCliente'),
    path('login', views.loginUsuario, name='loginUsuario'),
    path('logout', views.logoutUsuario, name='logoutUsuario'),

    path('identificarse', views.identificarse, name='identificarse'),
    path('continuarComoInvitado', views.continuarComoInvitado, name='continuarComoInvitado'),
    path('registrarPedido', views.registrarPedido, name='registrarPedido'),
    path('pago', views.pagoPedido, name='pagoPedido'),
    path('voucher/<int:pago_id>', views.voucherPago, name='voucherPago'),
    path('validar', views.validarPagos, name='validarPagos'),
    path('transportes', views.transportes, name='transportes'),
    path('validar/<int:pago_id>', views.validarPago, name='validarPago'),
    path('gracias', views.gracias, name='gracias'),
]
