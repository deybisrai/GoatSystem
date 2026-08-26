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
from .compras import Compra, CompraDetalle, Proveedor
from .inventario import Inventario
from .promociones import Campana, Cupon
from .ventas import Pedido, PedidoDetalle

__all__ = [
    'Atributo',
    'Campana',
    'Categoria',
    'Cliente',
    'Color',
    'Compra',
    'CompraDetalle',
    'Cupon',
    'Curva',
    'Inventario',
    'Pedido',
    'PedidoDetalle',
    'Producto',
    'ProductoVisibleManager',
    'Proveedor',
    'ValorAtributo',
    'Variante',
]
