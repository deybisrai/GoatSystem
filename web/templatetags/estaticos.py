"""
Etiqueta {% static_v %}: igual que {% static %} pero agrega la fecha del archivo
como version (?v=...).

Sirve para que el navegador no siga mostrando la imagen vieja cuando cambias el
logo: al cambiar el archivo cambia la version, y el navegador vuelve a pedirlo.
"""

from pathlib import Path

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def static_v(ruta):
    url = static(ruta)

    encontrado = finders.find(ruta)
    if not encontrado:
        return url

    try:
        version = int(Path(encontrado).stat().st_mtime)
    except OSError:
        return url

    separador = '&' if '?' in url else '?'
    return f'{url}{separador}v={version}'
