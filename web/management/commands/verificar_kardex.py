"""
Compara el saldo cacheado (`Inventario.stock`) contra la suma del kardex.

Los dos numeros tienen que decir lo mismo siempre. Si se separan es que alguien
movio stock por fuera de `Inventario.registrar_movimiento()`: una migracion, un
script suelto, un UPDATE a mano. Este comando lo detecta y, con --arreglar, deja
la diferencia registrada como ajuste en vez de taparla.
"""

from django.core.management.base import BaseCommand
from django.db.models import Sum

from web.models import Inventario, MovimientoInventario

MOTIVO_AJUSTE = (
    'Ajuste automatico de verificar_kardex: el stock se movio fuera del kardex '
    'y esta linea cierra la diferencia.'
)


class Command(BaseCommand):
    help = 'Verifica que el stock de cada unidad vendible coincida con su kardex'

    def add_arguments(self, parser):
        parser.add_argument(
            '--arreglar',
            action='store_true',
            help='Escribe un movimiento de ajuste por cada diferencia encontrada.',
        )

    def handle(self, *args, **opciones):
        items = (
            Inventario.objects
            .select_related('variante__producto', 'variante__color', 'valor')
            .annotate(segun_kardex=Sum('movimientos__cantidad'))
            .order_by('variante__producto__nombre', 'valor')
        )

        revisados = 0
        diferencias = []
        for item in items:
            revisados += 1
            kardex = item.segun_kardex or 0
            if kardex != item.stock:
                diferencias.append((item, kardex))

        for item, kardex in diferencias:
            self.stdout.write(self.style.WARNING(
                f'{item}: stock {item.stock}, kardex {kardex} '
                f'(diferencia {item.stock - kardex:+d})'
            ))
            if opciones['arreglar']:
                self._ajustar(item, kardex)
                self.stdout.write(self.style.SUCCESS('  -> ajuste registrado en el kardex'))

        if not diferencias:
            self.stdout.write(self.style.SUCCESS(
                f'{revisados} unidades revisadas: el kardex cuadra con el stock.'
            ))
            return

        self.stdout.write(
            f'{len(diferencias)} de {revisados} unidades no cuadran.'
            + ('' if opciones['arreglar'] else ' Corre con --arreglar para registrarlas.')
        )

    def _ajustar(self, item, kardex):
        """
        Escribe el movimiento que le falta al kardex para explicar el stock real.

        Es el unico lugar del proyecto que crea un movimiento sin pasar por
        `registrar_movimiento()`, y a proposito: aqui el stock ya cambio: lo que
        esta atrasado es el historial. Sumarlo otra vez lo dejaria peor.
        """
        MovimientoInventario.objects.create(
            item=item,
            tipo=MovimientoInventario.AJUSTE,
            cantidad=item.stock - kardex,
            stock_anterior=max(kardex, 0),
            stock_resultante=item.stock,
            costo_unitario=item.costo_promedio,
            costo_promedio_resultante=item.costo_promedio,
            motivo=MOTIVO_AJUSTE,
        )
