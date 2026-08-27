"""
Deja programado el proximo viaje a cada ciudad que no lo tenga.

Es la red contra el punto ciego del modulo: si nadie programa el transporte a
Lircay, esa ciudad deja de ofrecer productos y nadie se entera, porque el cliente
no ve un error, ve un catalogo mas chico. Nunca llega un reclamo por lo que no se
mostro.

Programar un viaje no compromete mercaderia: crea el documento vacio con la fecha
que ya declara la ubicacion. Lo que sube a la camioneta se sigue decidiendo a
mano, cerca de la salida.

Va en un cron semanal. Si un viaje no se va a hacer, se anula y listo.
"""

from django.core.management.base import BaseCommand

from web.models import Traslado, Ubicacion


class Command(BaseCommand):
    help = 'Crea el proximo traslado a cada ciudad que no tenga uno programado'

    def add_arguments(self, parser):
        parser.add_argument(
            '--simular', action='store_true',
            help='Solo muestra que ciudades quedarian sin transporte, sin crear nada.',
        )

    def handle(self, *args, **opciones):
        principal = Ubicacion.objects.filter(es_principal=True, activo=True).first()
        if principal is None:
            self.stdout.write(self.style.ERROR(
                'No hay un almacen principal marcado: no se sabe de donde saldrian los viajes.'
            ))
            return

        destinos = Ubicacion.objects.filter(activo=True).exclude(pk=principal.pk)
        sin_viaje = [u for u in destinos if u.sin_transporte]

        for ubicacion in destinos:
            viaje = ubicacion.proximo_traslado()
            if viaje is not None:
                self.stdout.write(
                    f'{ubicacion.nombre:<18} ya tiene viaje: sale {viaje.fecha_despacho:%d/%m}, '
                    f'llega {viaje.fecha_disponible:%d/%m}'
                )
            else:
                self.stdout.write(self.style.WARNING(
                    f'{ubicacion.nombre:<18} SIN TRANSPORTE PROGRAMADO '
                    f'(sus productos no se ofrecen ahi)'
                ))

        if not sin_viaje:
            self.stdout.write(self.style.SUCCESS('\nTodas las ciudades tienen su viaje.'))
            return

        if opciones['simular']:
            self.stdout.write(
                f'\n{len(sin_viaje)} ciudad(es) quedarian sin ofrecer productos. '
                'Corre sin --simular para programarles el viaje.'
            )
            return

        for ubicacion in sin_viaje:
            viaje = Traslado.objects.create(origen=principal, destino=ubicacion)
            self.stdout.write(self.style.SUCCESS(
                f'  programado {viaje}: sale {viaje.fecha_despacho:%d/%m}, '
                f'llega {viaje.fecha_disponible:%d/%m}'
            ))

        self.stdout.write(self.style.SUCCESS(
            f'\n{len(sin_viaje)} viaje(s) programados. Estan vacios: lo que sube se decide despues.'
        ))
