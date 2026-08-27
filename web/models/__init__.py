"""
Modelos de la tienda, separados por area de negocio.

El resto del proyecto sigue importando igual que antes:
    from web.models import Producto, Pedido, ...
"""

from .catalogo import (
    Atributo,
    Categoria,
    Color,
    Curva,
    Producto,
    ProductoVisibleManager,
    ValorAtributo,
    Variante,
)
from .clientes import Cliente
from .entregas import PuntoRecojo
from .compras import Compra, CompraBloqueada, CompraDetalle, Proveedor
from .inventario import Inventario
from .kardex import MovimientoInventario
from .pagos import CuentaRecaudadora, Pago
from .promociones import Campana, Cupon
from .traslados import Traslado, TrasladoDetalle
from .ubicaciones import Ubicacion
from .ventas import Pedido, PedidoDetalle

__all__ = [
    'Atributo',
    'Campana',
    'Categoria',
    'Cliente',
    'Color',
    'Compra',
    'CompraBloqueada',
    'CompraDetalle',
    'CuentaRecaudadora',
    'Cupon',
    'Curva',
    'Inventario',
    'MovimientoInventario',
    'Pago',
    'Pedido',
    'PedidoDetalle',
    'Producto',
    'ProductoVisibleManager',
    'Proveedor',
    'PuntoRecojo',
    'Traslado',
    'TrasladoDetalle',
    'Ubicacion',
    'ValorAtributo',
    'Variante',
]
