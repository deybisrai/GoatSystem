"""
Suelta las unidades de los pedidos que se pasaron del plazo para pagar.

El checkout ya hace esto por su cuenta para las filas que va a tocar, asi que el
sistema no depende de que este comando corra. Sirve para lo otro: que un producto
que nadie esta intentando comprar no quede reservado hasta que alguien lo intente,
y para dejar el barrido en un cron una vez por hora.
"""

from django.core.management.base import BaseCommand

from web.models import Pedido


class Command(BaseCommand):
    help = 'Cancela los pedidos con la reserva vencida y devuelve sus unidades a la venta'

    def add_arguments(self, parser):
        parser.add_argument(
            '--simular',
            action='store_true',
            help='Solo muestra que pedidos venceria, sin tocar nada.',
        )

    def handle(self, *args, **opciones):
        vencidos = list(Pedido.reservas_vencidas().prefetch_related('detalles'))

        if not vencidos:
            self.stdout.write(self.style.SUCCESS('No hay reservas vencidas.'))
            return

        for pedido in vencidos:
            unidades = sum(d.cantidad for d in pedido.detalles.all())
            self.stdout.write(
                f'{pedido.nro_pedido}  {pedido.get_estado_display():<14} '
                f'vencio {pedido.reserva_vence:%d/%m %H:%M}  '
                f'{unidades} unidad(es)  {pedido.email_comprador}'
            )

        if opciones['simular']:
            self.stdout.write(
                f'\n{len(vencidos)} pedido(s) venceria(n). Corre sin --simular para soltarlos.'
            )
            return

        cuantos = Pedido.vencer_reservas()
        self.stdout.write(self.style.SUCCESS(
            f'\n{cuantos} pedido(s) cancelados. Sus unidades vuelven a estar a la venta.'
        ))
