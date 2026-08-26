"""
Convierte el logo cuadrado de GOAT X (cabra arriba, texto abajo) en una
version horizontal para el header: cabra a la izquierda, texto al costado.

Tambien quita el fondo blanco para que no se vea un rectangulo sobre el header.

Uso:
    venv/Scripts/python.exe herramientas/generar_logo.py ruta/al/logo_original.png

Escribe el resultado en web/static/img/logo.png (guarda una copia del anterior).
"""

import shutil
import sys
from pathlib import Path

from PIL import Image

RAIZ = Path(__file__).resolve().parent.parent
DESTINO = RAIZ / 'web' / 'static' / 'img' / 'logo.png'

ALTURA_FINAL = 140          # se muestra a ~70px, al doble para pantallas retina
SEPARACION = 26             # espacio entre la cabra y el texto
TOLERANCIA_BLANCO = 235     # a partir de aqui se considera fondo


def quitar_fondo(imagen):
    """ Vuelve transparentes los pixeles casi blancos """
    imagen = imagen.convert('RGBA')
    rojo, verde, azul, alfa = imagen.split()
    limpios = [
        (r, g, b, 0) if r >= TOLERANCIA_BLANCO and g >= TOLERANCIA_BLANCO and b >= TOLERANCIA_BLANCO
        else (r, g, b, a)
        for r, g, b, a in zip(rojo.tobytes(), verde.tobytes(), azul.tobytes(), alfa.tobytes())
    ]
    limpia = Image.new('RGBA', imagen.size)
    limpia.putdata(limpios)
    return limpia


def filas_con_contenido(imagen):
    """ Que filas de pixeles tienen algo dibujado (no transparente) """
    ancho, alto = imagen.size
    datos = imagen.getchannel('A').tobytes()
    return [
        any(datos[y * ancho + x] > 20 for x in range(ancho))
        for y in range(alto)
    ]


def separar_cabra_y_texto(imagen):
    """
    Corta la imagen por el espacio en blanco mas grande entre bloques.
    Arriba queda la cabra, abajo el texto.
    """
    filas = filas_con_contenido(imagen)

    huecos, inicio = [], None
    for y, tiene in enumerate(filas):
        if not tiene and inicio is None:
            inicio = y
        elif tiene and inicio is not None:
            huecos.append((inicio, y))
            inicio = None

    # ignora los margenes: solo huecos entre contenido
    internos = [h for h in huecos if h[0] > 0 and h[1] < len(filas)]
    if not internos:
        raise SystemExit('No encontre separacion entre la cabra y el texto.')

    desde, hasta = max(internos, key=lambda h: h[1] - h[0])
    corte = (desde + hasta) // 2

    cabra = imagen.crop((0, 0, imagen.width, corte)).crop(
        imagen.crop((0, 0, imagen.width, corte)).getbbox()
    )
    texto = imagen.crop((0, corte, imagen.width, imagen.height))
    texto = texto.crop(texto.getbbox())
    return cabra, texto


def escalar_a_altura(imagen, altura):
    proporcion = altura / imagen.height
    return imagen.resize((max(1, round(imagen.width * proporcion)), altura), Image.LANCZOS)


def componer(cabra, texto):
    """ Cabra a la izquierda, texto al costado, ambos centrados verticalmente """
    cabra = escalar_a_altura(cabra, ALTURA_FINAL)
    texto = escalar_a_altura(texto, round(ALTURA_FINAL * 0.46))

    ancho = cabra.width + SEPARACION + texto.width
    lienzo = Image.new('RGBA', (ancho, ALTURA_FINAL), (0, 0, 0, 0))
    lienzo.paste(cabra, (0, 0), cabra)
    lienzo.paste(
        texto,
        (cabra.width + SEPARACION, (ALTURA_FINAL - texto.height) // 2),
        texto,
    )
    return lienzo


def main():
    if len(sys.argv) < 2:
        raise SystemExit('Indica la ruta del logo original.\n'
                         '  Ejemplo: venv/Scripts/python.exe herramientas/generar_logo.py C:/ruta/goat.png')

    origen = Path(sys.argv[1])
    if not origen.exists():
        raise SystemExit(f'No encuentro el archivo: {origen}')

    original = quitar_fondo(Image.open(origen))
    cabra, texto = separar_cabra_y_texto(original)
    resultado = componer(cabra, texto)

    if DESTINO.exists():
        respaldo = DESTINO.with_name('logo_anterior.png')
        shutil.copy2(DESTINO, respaldo)
        print(f'Logo anterior guardado en {respaldo.relative_to(RAIZ)}')

    resultado.save(DESTINO)
    print(f'Listo: {DESTINO.relative_to(RAIZ)}  ({resultado.width}x{resultado.height} px, fondo transparente)')
    print(f'  cabra: {cabra.width}x{cabra.height}   texto: {texto.width}x{texto.height}')


if __name__ == '__main__':
    main()
