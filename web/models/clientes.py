""" Clientes registrados (los invitados no generan fila aqui) """

from django.contrib.auth.models import User
from django.db import models


class Cliente(models.Model):
    """ Cuenta registrada. Un comprador invitado NO genera fila aqui. """
    usuario = models.OneToOneField(User, on_delete=models.RESTRICT)
    dni = models.CharField(max_length=8)
    sexo = models.CharField(max_length=1, default='M')
    telefono = models.CharField(max_length=20)
    direccion = models.CharField(max_length=200, blank=True)
    fecha_nacimiento = models.DateField(null=True, blank=True)

    def __str__(self):
        return self.dni
