"""
Que se le puede prometer al cliente en cada punto de recojo.

Tres respuestas posibles, y ninguna es un rango vago:

  hoy           hay stock en esa ciudad
  encargo       esta en otra ciudad y hay transporte programado: se le da una
                FECHA concreta, no "1 a 3 dias habiles"
  lejos         esta en otra ciudad y no se traslada (una refrigeradora no viaja)
  sin_viaje     esta en otra ciudad y no hay transporte programado

La fecha sale del transporte programado, y solo de ahi. Sin camioneta no hay
promesa: es preferible no ofrecer el producto en esa ciudad antes que prometer
una fecha que depende de acordarse de programar el viaje.
"""

from .models import Traslado

HOY = 'hoy'
ENCARGO = 'encargo'
LEJOS = 'lejos'
SIN_STOCK = 'sin_stock'
SIN_VIAJE = 'sin_viaje'

# las tres formas de que un punto no sirva para este pedido
IMPOSIBLES = (LEJOS, SIN_STOCK, SIN_VIAJE)


def _en_otras_ciudades(item, destino):
    """
    El mismo SKU fuera de la ciudad donde el cliente quiere retirarlo.

    Se excluye por ubicacion, no por fila: el item que llega desde el carrito
    puede ser el de cualquier ciudad, y lo que interesa es que haya unidades en
    algun lado que no sea el destino.
    """
    from .models import Inventario

    return (
        Inventario.objects
        .filter(variante_id=item.variante_id, valor_id=item.valor_id)
        .exclude(ubicacion=destino)
        .select_related('ubicacion')
    )


def _proximo_viaje(destino):
    """
    El proximo transporte que llega a esa ciudad, si hay alguno programado.

    No hay calculo de respaldo a proposito. Si nadie programo el viaje, no hay
    fecha que prometer, y decirle al cliente "1 a 3 dias" seria adivinar.
    """
    return (
        Traslado.objects
        .filter(
            destino=destino,
            estado__in=[Traslado.PLANIFICADO, Traslado.EN_TRANSITO],
        )
        .order_by('fecha_disponible')
        .first()
    )


def para_item(item, cantidad, punto):
    """
    Que se le promete a este cliente por esta unidad en este punto.
    Devuelve (clave, fecha). La fecha solo viene con ENCARGO.
    """
    from .models import Inventario

    destino = punto.ubicacion

    # el item que trae el carrito puede ser el de cualquier ciudad: lo que importa
    # es si hay unidades libres en la ciudad del punto elegido
    aca = (
        Inventario.objects
        .filter(variante_id=item.variante_id, valor_id=item.valor_id, ubicacion=destino)
        .first()
    )
    if aca is not None and aca.disponible >= cantidad:
        return HOY, None

    if not item.variante.producto.se_traslada:
        return LEJOS, None

    afuera = [h for h in _en_otras_ciudades(item, destino) if h.disponible >= cantidad]
    if not afuera:
        return SIN_STOCK, None

    viaje = _proximo_viaje(destino)
    if viaje is None:
        return SIN_VIAJE, None

    return ENCARGO, viaje.fecha_disponible


def para_carrito(lineas, punto):
    """
    Lo mismo pero para todo el carrito: manda el peor caso.

    Si una sola linea no viaja, el pedido entero no se puede retirar ahi. Y si
    varias son por encargo, la fecha es la mas lejana: el cliente retira una vez,
    no una por producto.
    """
    peor, fecha = HOY, None
    for item, cantidad in lineas:
        clave, cuando = para_item(item, cantidad, punto)

        if clave in IMPOSIBLES:
            return clave, None
        if clave == ENCARGO:
            peor = ENCARGO
            fecha = cuando if fecha is None else max(fecha, cuando)
    return peor, fecha
