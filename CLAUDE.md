# GOAT X — tienda virtual

Ecommerce en Django 5.2 + MySQL para una tienda multirubro: zapatillas, ropa,
celulares, laptops, TVs, electrodomesticos.

Roadmap y estado: https://claude.ai/code/artifact/882e8434-8427-496c-b736-c8fc63be7839

## Como correr

```bash
venv/Scripts/python.exe manage.py runserver
venv/Scripts/python.exe manage.py test web      # 386 tests
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
diferencia sigue sin modelarse y no hace falta: lo que el sistema necesita saber
es de que ciudad se sirve cada mostrador, y eso es `PuntoRecojo.ubicacion`,
obligatoria desde la migracion 0028. La ubicacion manda. Cuatro mostradores,
dos ciudades.

**Un pedido reserva, no descuenta.** `crear_pedido()` llama a
`Inventario.reservar()`, que sube `reservado` sin tocar `stock` ni el kardex. El
pedido nace con `reserva_vence` (`MINUTOS_RESERVA`, hoy 10). Solo
`cambiar_estado(PAGADO)` convierte la reserva en venta, y es el unico lugar
donde eso pasa, venga por donde venga.

**Una reserva vencida deja de contar al leer, no cuando alguien la barre.**
`disponible = stock - reservado + reservado_vencido`. El contador `reservado`
solo baja cuando se cancela el pedido, asi que sin ese tercer termino la ultima
unidad quedaba trabada para siempre: el catalogo la daba por agotada, el carrito
no dejaba agregarla, y lo unico que la soltaba vivia detras del checkout, al que
no se llegaba. **El plazo se cumple con el reloj; el papeleo puede esperar.**

**Con el comprobante arriba, la unidad se congela sin plazo.** `declarar_pago()`
apaga `reserva_vence` en vez de reprogramarlo. `HORAS_VALIDACION` es la promesa
que le mostramos al cliente, no un vencimiento: el ya transfirio, y soltarle la
unidad porque nosotros tardamos seria castigarlo por nuestra demora. Lo que si
corre es `HORAS_ALERTA_VALIDACION`, que pinta el pago como demorado en la bandeja.

**Dos identificadores, y la diferencia importa.** `codigo_reserva` (`R-A3F9C2D1`)
nace al confirmar el checkout y **no es correlativo**: un carrito abandonado no
tiene por que gastarse un numero de la serie. `nro_pedido` (`P20260830-00001`) se
emite recien al recibir el comprobante, es correlativo por dia, y **una vez
emitido no se reutiliza jamas** aunque el pedido se cancele. `referencia` devuelve
el que corresponda.

**Se le acabo el tiempo no es lo mismo que lo cancelaron.** `EXPIRADO` es el
cliente que no llego; `CANCELADO` es una decision de alguien -- un comprobante
rechazado o el admin. Se cuentan por separado: sin eso no hay forma de medir si
el plazo esta bien puesto. `cerrado_sin_venta` los agrupa cuando conviene
tratarlos igual.

**Un comprobante solo existe sobre un pedido vivo.** `declarar_pago()` se niega
sobre uno expirado o cancelado, y validar tampoco lo resucita. Si el cliente
pago de verdad, rehace la compra y sube el mismo voucher sobre el pedido nuevo;
si otro se llevo la unidad, lo resuelve un asesor por WhatsApp. Antes habia un
`revivir_por_pago_tardio()` que lo traia de vuelta solo, y era la unica
transicion del proyecto que iba para atras.

**El mismo clic no crea dos pedidos.** El formulario del checkout lleva un
`token_checkout` oculto y unico en base. El bloqueo de filas impide que dos
clientes se lleven la misma unidad; esto impide que un cliente se lleve dos
pedidos por apretar dos veces. Son problemas distintos. La garantia esta en el
`unique` y no en una consulta previa: entre consultar y crear hay una rendija.

**Un pedido nuevo suelta la reserva anterior de la misma sesion.** El cliente
que vuelve al catalogo y arranca de nuevo dejaba la reserva vieja huerfana:
retenia unidades diez minutos, pero la sesion, el aviso y `/pago` apuntaban todos
al nuevo, asi que nadie iba a pagarla. Y no habia limite: cinco vueltas, cinco
reservas vivas. La vieja termina en `EXPIRADO` con el motivo diciendo cual la
reemplazo. **La que ya tiene comprobante no se toca**: ahi hay plata de por medio.

**Un plazo corto es peor que uno largo.** Si la reserva vence a mitad de la
transferencia, el cliente ya movio plata y hay que devolversela. Un plazo largo
solo bloquea una unidad un rato.

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
- **`{# #}` es un comentario de UNA linea.** Con varias, Django imprime el resto
  como texto en la pagina. Los multilinea van con `{% comment %}`. Paso en el
  aviso flotante y se vio recien mirando la pantalla.
- **Los nombres de `@keyframes` son globales.** Redefinir uno dentro de un media
  query no lo acota a ese ancho. Y una animacion nunca deberia tocar `transform`
  si el `transform` es lo que posiciona al elemento: el aviso flotante quedaba
  14px corrido y se salia de la pantalla en celular.
- **Un test puede fijar un bug en vez de un comportamiento.** Habia uno que
  afirmaba que una reserva vencida seguia bloqueando su unidad. Pasaba en verde y
  documentaba el defecto como si fuera la regla.
- **Los tests prueban las piezas; los defectos viven en la costura.** Los cuatro
  problemas mas serios de la fase 7 se encontraron mirando datos reales o
  probando en el navegador, no corriendo la suite.
- **`escapejs` convierte el guion en `\u002D`.** Un codigo `R-XXXX` no aparece
  literal en el HTML de un mensaje flash. El navegador lo muestra bien; los tests
  tienen que mirar el mensaje, no el HTML.

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
| `web/avisos.py`            | Los seis correos al cliente, el aviso al equipo y el enlace al asesor |
| `web/middleware.py`        | Barre las reservas vencidas con el trafico normal |
| `web/context_processors.py`| El aviso flotante del pedido que quedo esperando pago |
| `web/management/commands/verificar_kardex.py` | Compara el saldo cacheado contra el historial |
| `web/management/commands/vencer_reservas.py` | Suelta las reservas de los pedidos que no pagaron a tiempo |
| `web/management/commands/programar_transportes.py` | Programa el proximo viaje a la ciudad que no lo tenga |
| `herramientas/generar_logo.py` | Arma el logo horizontal desde el cuadrado |

## Estado

**6 de 11 fases cerradas y la 7 practicamente terminada.** 386 tests, 23 modelos,
33 migraciones. La tienda vende, cobra y entrega de punta a punta:

- catalogo con variantes, carrito, cupones y checkout de invitado
- ciclo auditable: cada unidad tiene un documento detras (fase 6)
- cobro por transferencia con validacion humana (fase 7)
- entrega a domicilio o recojo en cuatro mostradores
- stock por ciudad y transporte entre ellas
- al cliente se le escribe en cada paso, sin que tenga que preguntar

Falta para lanzar: **8 seguridad** y **9 despliegue**. Despues 10 panel
administrativo (Django + HTMX) y 11 punto de venta con caja y boveda.

### Los dos negocios

Son dos operaciones sobre los mismos modelos, y conviene no mezclarlas:

- **Tienda online**: opera 24 horas y nunca toca un billete. Se cobra por
  transferencia y el dinero cae a nuestras cuentas. No tiene caja.
- **Tienda fisica**: abre y cierra su dia con efectivo bajo custodia de una
  persona. Necesita caja, boveda, billetaje y cierre de dia (fase 11).

La caja no cuenta ventas: **custodia efectivo**. Por eso una venta con tarjeta o
Yape tampoco entra a caja, aunque sea presencial. El corte no es online contra
fisico, es efectivo contra no-efectivo.

Cuando exista el dia operativo seran **dos relojes independientes**: `DiaOperativo`
gobierna solo lo que toca efectivo o almacen, y la tienda online se rige por la
fecha calendario real. Si el personal no cierra caja, la tienda fisica se bloquea
y la online sigue vendiendo.

### Lo que hace la fase 7

**Cobro sin pasarela.** El cliente paga a nuestras cuentas, declara su numero de
operacion y sube una captura. Alguien lo valida mirando la cuenta real. Cero
comision contra el 3-4% de una pasarela, a cambio de trabajo humano.

Cuentas cargadas (editables desde el admin, sembradas en la 0017):

| Metodo | Datos |
|---|---|
| QR Yape | Monetix Retail, `cuentas/qr_yape_retail.png` |
| Yape | 955134139 |
| BCP corriente | 3507296754036, CCI 00235000729675403679 |

**Plazos** (en `settings.py`): `MINUTOS_RESERVA` 10, `HORAS_VALIDACION` 12,
`HORAS_ALERTA_VALIDACION` 6, `SEGUNDOS_ENTRE_BARRIDOS` 60.

**El plazo no depende de que alguien corra un comando.** `VencerReservasMiddleware`
barre las vencidas aprovechando el trafico normal, como maximo una vez por minuto
(`cache.add()` decide quien barre, que es atomico; un `if not existe` no lo es).
`vencer_reservas` sigue existiendo para cuando la tienda esta quieta. Y aunque
nadie barra, `disponible` ya descuenta lo vencido al leer: el barrido ordena el
papeleo, no habilita la venta.

**Pantallas propias, fuera del admin:**

- `/pago` — el cliente elige cuenta, ve el QR, sube su comprobante y tiene el
  WhatsApp del asesor a mano
- `/validar` — bandeja de comprobantes hecha para el celular, ordenada por
  antiguedad; ahi llega el enlace del correo de aviso
- `/transportes` — que ciudad quedo sin viaje programado

**El aviso flotante es el camino de vuelta a `/pago`.** No hay ninguna URL que
lleve ahi: se encuentra por la sesion. Quien se iba a mirar otro producto perdia
el rastro y se le vencia la reserva sin enterarse. El aviso lo sigue en toda la
tienda con su contador, y desaparece solo en `/pago` (ahi ya hay uno) y cuando el
pedido deja de estar esperando pago.

**Seis correos al cliente**, todos en `avisos.py` y todos con `on_commit` y
`fail_silently`: comprobante recibido, pago confirmado, listo para recojo,
enviado, entregado, comprobante rechazado. Un correo caido no puede voltear la
transaccion que ya se confirmo -- el pedido vale mas que el aviso.

**No hay contra-entrega** (decidido el 2026-08-27). Pagar en billetes al
motorizado genera efectivo bajo custodia, o sea una caja con otro nombre, y eso
llega bien hecho en la fase 11.

### Lo que quedo abierto

**Si conviene reservar.** La pregunta es de fondo y esta sin decidir: la reserva
existe para que dos clientes no se lleven la misma unidad en el mismo segundo,
pero eso ya lo garantiza el bloqueo de filas al confirmar el pago. Lo que agrega
la reserva es un plazo, y un plazo trae de arrastre todo lo demas -- el
vencimiento, el barrido, el contador, el aviso, el reemplazo, el estado
EXPIRADO. Sacarla simplifica mucho y cambia la promesa: el primero que sube el
comprobante se lo lleva. La rama `sin-reservas` esta creada para probarlo, y el
tag `antes-de-quitar-reservas` marca el punto de vuelta.

**El cliente no puede volver a agregar lo que el mismo reservo.** Su reserva le
come su propio `disponible`, asi que el catalogo le dice agotado a un producto
que tiene guardado. Si igual arma otro pedido, la reserva vieja se suelta y el
producto NO queda en el nuevo. Esta verificado y hace dano; la solucion depende
de la decision de arriba.

**El stock vuelve a la ciudad equivocada al cancelar.** Si el pedido reservo en
Huancavelica y se cancela, el `liberar()` no siempre acierta la ubicacion.
Pendiente de arreglar.

**Buscar un pedido por su codigo.** El aviso flotante vive en la sesion, asi que
no cubre el cambio de dispositivo. Hace falta una pantalla que reciba
`R-XXXXXXXX` o `PYYYYMMDD-XXXXX` y muestre el estado, y despues un "Mis pedidos"
completo.

**El horario del punto de recojo, estructurado** (hora de apertura y cierre) para
poder decir "retiralo hoy hasta las 10pm". Se decidio que por ahora no hace
falta: dice "Retiralo hoy" con el horario a la vista al lado.

### El estado del repositorio

Rama `fase-7-reserva-y-pedido-formal`, ultimo commit
`49a155e Un pedido nuevo suelta la reserva anterior de la misma sesion`. Trae la
separacion entre reserva y pedido formal, los correos al cliente y el aviso
flotante (migraciones 0029 a 0033). Todo esta empujado a GitHub. `main` sigue en
`e5711da` a la espera del merge.

Antes de tocar el modelo de reservas se dejaron dos puntos de vuelta: el tag
`antes-de-quitar-reservas`, la rama `sin-reservas` para el experimento, y un
volcado de la base en `~/Documents/respaldos-goatx/`.

Conviene arrancar mirando `git status`: esta linea envejece sola.

## Bloqueantes de produccion

En `goatsystem/settings.py`: `SECRET_KEY` commiteada (hay que rotarla),
`DEBUG = True`, `ALLOWED_HOSTS` vacio, y MySQL con usuario `root` sin password.
