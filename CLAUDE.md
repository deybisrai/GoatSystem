# GOAT X — tienda virtual

Ecommerce en Django 5.2 + MySQL para una tienda multirubro: zapatillas, ropa,
celulares, laptops, TVs, electrodomesticos.

Roadmap y estado: https://claude.ai/code/artifact/882e8434-8427-496c-b736-c8fc63be7839

## Como correr

```bash
venv/Scripts/python.exe manage.py runserver
venv/Scripts/python.exe manage.py test web      # 284 tests
venv/Scripts/python.exe manage.py verificar_kardex   # stock vs historial
venv/Scripts/python.exe manage.py vencer_reservas    # suelta lo no pagado a tiempo
venv/Scripts/python.exe manage.py programar_transportes  # que ninguna ciudad quede sin viaje
```

El idioma del proyecto es espanol: nombres de modelos, campos, mensajes y
comentarios. Sin tildes en el codigo (los datos si las llevan).

## Estructura de datos

El catalogo tiene tres niveles. Confundirlos es el error mas facil de cometer:

```
Producto        "Campus 00s"            <- lo que el cliente reconoce. Sin precio ni stock.
  Variante      SKU JP9163, color NEGRO <- un color. Aqui vive el precio.
    Inventario  talla 40, stock 3       <- lo que se compra y se vende. Aqui vive el stock.
```

- **El precio vive en `Variante`**, no por talla: en la practica el precio no
  cambia entre tallas del mismo color. `Inventario.precio_venta_override` existe
  para la excepcion rara y casi siempre esta vacio.
- **`stock` no es lo que se puede vender.** `stock` es lo que hay en el estante;
  `reservado` es lo comprometido por pedidos sin pagar; `disponible` es la resta,
  y es lo unico que el catalogo y el carrito deben mirar. Reservar **no** escribe
  kardex: es una promesa, no un movimiento. La `VENTA` se escribe una sola vez,
  al validar el pago.
- **Todo movimiento de stock queda escrito.** Cada entrada y salida deja una
  fila en `MovimientoInventario` (el kardex) con su saldo resultante y el
  documento que la respalda. `Inventario.stock` es el saldo cacheado que lee el
  catalogo; el kardex es el libro que lo explica. `verificar_kardex` compara los
  dos y avisa si se separaron.
- **`Inventario.valor` es opcional.** Una zapatilla lo usa (talla 40), un celular
  tambien (capacidad 128GB), una licuadora no (una sola fila, sin valor).

### Atributos por categoria

Cada `Categoria` declara que diferencia sus unidades vendibles:

| Categoria        | atributo  | usa_genero |
|------------------|-----------|------------|
| ZAPATILLAS       | Talla     | si         |
| CELULARES        | Memoria   | no         |
| ELECTRODOMESTICOS| (ninguno) | no         |

Los valores viven en `ValorAtributo` (40, 41, 8GB/256GB). Las `Curva` agrupan
los que se compran juntos (Dama = 35 a 38.5) y declaran donde aplican
(categoria + genero), para que al registrar una zapatilla de mujer se ofrezcan
8 opciones y no las 38 del catalogo completo.

Un celular con RAM y almacenamiento usa **un valor combinado** ('8GB/256GB'),
no dos atributos. Fue una decision consciente: es como el fabricante vende el SKU.

## Reglas que no se deben romper

**El pedido es una fotografia, no una referencia.** `PedidoDetalle` copia sku,
nombre, valor y precio al momento de la venta. Si manana sube el precio, la
boleta de ayer no cambia. Lo mismo con la direccion de envio en `Pedido`.

**Nunca se borra, se desactiva.** `activo` en Producto/Variante/Categoria. Un
producto descontinuado sigue existiendo porque hay pedidos que lo referencian.

**El precio manda desde la base de datos.** El carrito vive en la sesion del
navegador, asi que no se confia en el. `pedidos.crear_pedido()` compara contra
el precio real y aborta si cambio, en vez de cobrar un monto distinto al que
el cliente vio. Lo mismo con los cupones.

**El stock vive en una ciudad.** `Inventario` lleva `ubicacion`, y el mismo SKU
existe una vez por ciudad. Un punto de recojo **no** es una ubicacion: GOAT X,
Daily Credits y Vision para crecer son tres mostradores de Huancavelica que se
sirven del mismo almacen. La ubicacion es la ciudad.

**El traslado planifica el viaje, no la mercaderia.** El documento nace vacio y
no reserva nada. Reservar el lunes lo que sale el sabado bloquea cinco dias un
par que nadie compro todavia, y se pierden ventas seguras donde esta. Las lineas
se suman despues: **por pedido** las trae la venta (ya vienen reservadas por su
pedido), **por stock** las agregas vos, cerca de la salida.

**Agregar una ciudad es una fila, no codigo.** Pampas o Huancayo entran creando
una `Ubicacion` con su dia de despacho y sus dias de viaje. La pantalla
`/transportes`, el comando y la disponibilidad los toman solos.

**Sin transporte programado no hay promesa.** Si nadie programo el viaje a Lircay,
sus productos no se ofrecen ahi. Es preferible no vender que prometer una fecha
que depende de acordarse.

**El punto ciego del transporte se mira, no se espera.** Si nadie programa el
viaje a una ciudad, esa ciudad deja de ofrecer productos y **nadie reclama**,
porque el cliente no ve un error: ve un catalogo mas chico. Por eso existe
`/transportes` y el comando `programar_transportes`.

**Lo vendido deja de ser inventario.** Cuando se valida el pago, la unidad sale
del stock de su ciudad con su `VENTA` en el kardex. Si tiene que viajar, viaja
como mercaderia del cliente: la linea `POR_PEDIDO` del traslado no escribe kardex
porque ya se registro al venderla.

**Quien recibe cuenta, no acepta.** `Traslado.recibir()` toma el conteo real y
deja escrito el faltante. Tercera vez que el proyecto llega al mismo patron:
compra, remesa a boveda y ahora traslado.

**El ciclo del pedido se bifurca despues de Pagado.** Con envio va a `Enviado`;
con recojo va a `Listo para recojo`, que es cuando el producto ya esta en el
mostrador. Recien ahi el cliente tiene una fecha: el sistema **nunca promete un
plazo de traslado**, porque depende de la disponibilidad del momento.

**El pedido copia el punto de recojo, no solo lo referencia.** `punto_recojo_nombre`
y `punto_recojo_direccion` son fotografia, igual que el SKU: si manana cierra esa
agencia, el pedido de ayer sigue diciendo donde se retiro.

**Un punto de recojo no es un almacen.** GOAT X (Huancavelica) y Monetix (Lircay)
guardan mercaderia; Daily Credits y Vision para crecer solo atienden. Esa
diferencia se modela cuando llegue el stock por ubicacion, no antes.

**Un pedido reserva, no descuenta.** `crear_pedido()` llama a
`Inventario.reservar()`, que sube `reservado` sin tocar `stock` ni el kardex. El
pedido nace con `reserva_vence` (`MINUTOS_RESERVA`, hoy 15). Enviar el
comprobante detiene ese reloj y lo cambia por el nuestro (`HORAS_VALIDACION`).
Solo `cambiar_estado(PAGADO)` convierte la reserva en venta, y es el unico lugar
donde eso pasa, venga por donde venga.

**Un plazo corto es peor que uno largo.** Si la reserva vence a mitad de la
transferencia, el cliente ya movio plata y hay que devolversela. Un plazo largo
solo bloquea una unidad un rato. Por eso 15 minutos y no 3.

**El stock solo se mueve por `Inventario.registrar_movimiento()`.** Relee la
fila con `select_for_update()` dentro de la transaccion, recalcula el costo
promedio y escribe el kardex, todo junto. Un `UPDATE` directo o un `stock += 1`
sueltos rompen la unica garantia que sostiene la fase 6.

**Una compra aplicada no se edita ni se borra: se anula.** `Compra.anular()`
saca las unidades que trajo y deja el rastro. Editarla dejaba la factura y el
almacen diciendo cosas distintas; borrarla dejaba unidades sin documento.

**Un pedido no se borra: se cancela.** `Pedido.cancelar()` devuelve el stock,
libera el uso del cupon y conserva el detalle. Los estados solo avanzan
(`Pedido.TRANSICIONES`), y un pedido entregado ya no se cancela.

**Las campanas y cupones nunca tocan `precio_venta`.** El descuento se calcula
al vuelo; asi no hay que acordarse de revertir precios cuando termina la campana.

## Trampas conocidas

- **MySQL no soporta `UniqueConstraint` con `condition`.** Django lo omite en
  silencio. La regla "una sola fila de inventario sin valor" se valida en
  `Inventario.clean()`, no en la base de datos.
- **MySQL ignora mayusculas en campos unique.** No se puede crear el color
  'Negro' si ya existe 'NEGRO'. Es util: bloquea duplicados por tipeo.
- **Nunca renombrar un archivo de migracion despues de aplicarlo.** Django la
  registra por nombre y la creera pendiente. Ya paso una vez con la 0011.
- **Al renombrar un modelo, mover su plantilla del admin.** Django la busca en
  `admin/web/<modelo>/change_list.html`. Los totales del inventario
  desaparecieron en silencio cuando `VarianteTalla` paso a `Inventario`.
- **El carrito en sesion sobrevive a los refactors.** `Cart._normalizar()`
  convierte carritos guardados con nombres viejos. Si se renombra un campo del
  carrito, hay que agregarlo ahi o los clientes con carrito abierto veran un error.
- **Un archivo bajo `MEDIA_ROOT` es publico, punto.** Django en desarrollo y
  Nginx en produccion lo entregan a quien adivine la ruta, sin preguntar nada.
  Una vista con permisos no alcanza si el archivo tambien se puede pedir por su
  URL. Por eso los vouchers viven en `PRIVADO_ROOT` con `AlmacenPrivado`, cuyo
  `.url()` lanza a proposito.
- **El admin le pide `.url` a un FileField de solo lectura.** Pintar el voucher
  crudo en la ficha de `Pago` reventaba la pantalla. Va como un metodo display
  que enlaza a `web:voucherPago`.
- **Reservar y vender son cosas distintas y se rompen distinto.** Un
  `descontar_stock()` en el checkout vuelve a llenar el kardex de ventas que
  nunca ocurrieron (paso: 31% de los movimientos eran fantasma). Si hace falta
  comprometer unidades, es `reservar()`.
- **El kardex se puede separar del stock sin que nadie avise.** Un
  `queryset.update(stock=...)` o un UPDATE a mano no escriben movimiento. El
  unico lugar del proyecto que crea un movimiento sin mover stock es
  `verificar_kardex --arreglar`, y es a proposito: ahi el stock ya cambio y lo
  que esta atrasado es el historial.
- **`PuntoRecojo.provincia` y `PuntoRecojo.ubicacion` dicen lo mismo y ya se
  contradijeron.** La provincia es texto de la direccion y la ubicacion es la
  autoridad para la logica. Cargando 'Huancavelica' (el departamento) en la
  provincia de Monetix, la migracion la emparejo con la ciudad equivocada. Si se
  toca esto, la ubicacion manda.
- **Un `verificar_editable()` en `save()` bloquea tambien lo que si se puede
  escribir.** Registrar lo que llego de un traslado en transito es legitimo: se
  permite solo cuando `update_fields` se limita a los campos de la recepcion.
- **El `hidden` de HTML pierde contra el CSS del theme.** `[hidden]{display:none}`
  es regla del navegador, y cualquier clase de autor con `display` le gana. Paso
  en el checkout: el bloque de direccion lleva `.campos`, que el theme pinta como
  grid, y seguia visible con el recojo elegido. Se arregla con un selector propio
  (`.entrega-envio[hidden]`), que gana por especificidad sin `!important`.
- **Agregar un templatetag nuevo exige reiniciar el servidor.** Django carga las
  librerias al arrancar.

## Archivos

| Archivo | Rol |
|---|---|
| `web/models/catalogo.py`   | Categoria, Atributo, ValorAtributo, Curva, Color, Producto, Variante |
| `web/models/inventario.py` | Inventario: stock y costo por unidad vendible |
| `web/models/kardex.py`     | MovimientoInventario: por que cambio cada stock |
| `web/models/pagos.py`      | CuentaRecaudadora y Pago: cobro por transferencia |
| `web/models/entregas.py`   | PuntoRecojo: los mostradores donde el cliente retira |
| `web/models/ubicaciones.py`| Ubicacion: las ciudades donde vive el stock |
| `web/models/traslados.py`  | Traslado: el viaje entre ciudades, con dos manos |
| `web/disponibilidad.py`    | Que se le promete al cliente en cada mostrador |
| `web/models/compras.py`    | Proveedor, Compra, CompraDetalle (entra stock) |
| `web/models/ventas.py`     | Pedido, PedidoDetalle (sale stock) |
| `web/models/promociones.py`| Campana, Cupon |
| `web/carrito.py`           | Carrito en sesion |
| `web/pedidos.py`           | Convierte el carrito en venta. Aparte de views porque el POS lo reutilizara |
| `web/management/commands/verificar_kardex.py` | Compara el saldo cacheado contra el historial |
| `herramientas/generar_logo.py` | Arma el logo horizontal desde el cuadrado |

## Estado

6 de 11 fases cerradas. La tienda vende de punta a punta (catalogo, carrito,
cupones, checkout de invitado, descuento de stock) y ahora el ciclo es
auditable: cada unidad tiene un documento detras y toda correccion deja rastro.
Sigue **sin cobro**: el pedido nace en estado "Solicitado".

Son dos negocios sobre los mismos modelos, y conviene no mezclarlos:

- **Tienda online**: opera 24 horas y nunca toca un billete. El cliente paga con
  pasarela; el dinero vive ahi y se liquida al banco dias despues. No tiene caja.
- **Tienda fisica**: abre y cierra su dia con efectivo bajo custodia de una persona.
  Necesita caja, boveda, billetaje y cierre de dia.

La caja no cuenta ventas: **custodia efectivo**. Por eso una venta con tarjeta o
Yape tampoco entra a caja, aunque sea presencial. El corte no es online contra
fisico, es efectivo contra no-efectivo.

Cuando exista el dia operativo, seran **dos relojes independientes**: `DiaOperativo`
gobierna solo lo que toca efectivo o almacen, y la tienda online se rige por la
fecha calendario real. Si el personal no cierra caja, la tienda fisica se bloquea
y la online sigue vendiendo.

**En curso: Fase 7 — cobro por transferencia.** Sin pasarela: el cliente paga a
nuestras cuentas (BCP, Yape, QR), declara su numero de operacion y sube una
captura. Alguien lo valida despues mirando la cuenta real.

Ya hecho: `CuentaRecaudadora` y `Pago`, el estado `EN_VALIDACION`, la pantalla
`/pago` con cuenta regresiva, la bandeja de validacion en el admin, el almacen
privado de vouchers, la reserva con vencimiento (`reservado` separado de `stock`,
comando `vencer_reservas`, pago fuera de plazo), y el **recojo en tienda** con los
cuatro puntos y el estado `LISTO_RECOJO`.

**Validar es contar, no aceptar.** `Pago.validar()` exige el monto que se vio en
la cuenta; no hay boton que acepte el que declaro el cliente. Mismo criterio con
el que la boveda confirmara una remesa en la fase 11.

**El voucher es evidencia, no prueba.** Una captura de Yape se falsifica en
segundos. La prueba es el extracto. No se despacha sin ver la plata.

**Un pedido no llega a Pagado sin un `Pago` detras.** Se quito la accion "marcar
como pagado" del admin a proposito: todo movimiento tiene documento.

**No hay contra-entrega** (decidido el 2026-08-27). Pagar en billetes al
motorizado genera efectivo bajo custodia, o sea una caja con otro nombre, y eso
llega bien hecho en la fase 11. La tienda online cobra solo por transferencia.

El stock por ubicacion y los traslados **ya estan hechos** (se adelantaron desde
la fase 10 porque el recojo en Lircay los necesitaba).

Despues: 8 seguridad, 9 despliegue (**la tienda online sale a produccion aqui**),
10 panel administrativo con Django + HTMX, 11 punto de venta y caja.

**Pendiente de la fase 10:** hoy el stock es uno solo, sin ciudad. GOAT X es el
almacen general y Monetix Lircay tambien guarda; mover mercaderia entre ellos es
un movimiento de inventario real y va a necesitar su documento con dos pasos
(quien envia declara, quien recibe confirma), igual que la remesa a boveda. La
regla de negocio decidida: **se ofrece el producto en todas las ciudades**, pero
la pantalla distingue "retiralo hoy" de "por encargo, te confirmamos la fecha".

## Bloqueantes de produccion

En `goatsystem/settings.py`: `SECRET_KEY` commiteada (hay que rotarla),
`DEBUG = True`, `ALLOWED_HOSTS` vacio, y MySQL con usuario `root` sin password.
