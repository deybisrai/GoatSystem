from django import forms

class DateInput(forms.DateInput):
    input_type = 'date'

class ClienteForm(forms.Form):
    SEXO_CHOICES = (
        ('M','Masculino'),
        ('F','Fenenino'),
    )

    dni = forms.CharField(label='DNI',max_length=8)
    nombre = forms.CharField(label='Nombres',max_length=200,required=True)
    apellidos = forms.CharField(label='Apellidos',max_length=200,required=True)
    email = forms.EmailField(label='Email',required=True)
    direccion = forms.CharField(label='Direccion',widget=forms.Textarea)
    telefono = forms.CharField(label='Telefono',max_length=20)
    sexo = forms.ChoiceField(label='Sexo',choices=SEXO_CHOICES)
    fecha_nacimiento = forms.DateField(label='Fecha Nacimiento', input_formats=['%Y-%m-%d'],widget=DateInput())


class PedidoForm(forms.Form):
    """ Datos del checkout. Sirve igual para cliente registrado que para invitado. """

    # viaja oculto y vuelve igual en cada reenvio del mismo formulario: es lo
    # que deja reconocer un doble clic. Opcional a proposito: una pagina vieja
    # sin el campo tiene que poder comprar igual.
    token_checkout = forms.CharField(
        max_length=32, required=False, widget=forms.HiddenInput,
    )

    nombre = forms.CharField(label='Nombres', max_length=60)
    apellidos = forms.CharField(label='Apellidos', max_length=60)
    email = forms.EmailField(label='Email')
    telefono = forms.CharField(label='Telefono', max_length=20)
    dni = forms.CharField(label='DNI', max_length=8, required=False)

    # Envio o recojo. Los campos de direccion no son obligatorios de entrada:
    # se exigen en clean(), y solo si el cliente eligio que se lo lleven.
    modo_entrega = forms.ChoiceField(
        label='Como quieres recibirlo', widget=forms.RadioSelect, choices=(),
    )
    punto_recojo = forms.ModelChoiceField(
        label='Donde lo retiras', queryset=None, required=False,
        widget=forms.RadioSelect, empty_label=None,
    )

    direccion = forms.CharField(label='Direccion', max_length=200, required=False)
    referencia = forms.CharField(
        label='Referencia', max_length=200, required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Ej. frente al parque, casa azul'})
    )
    departamento = forms.CharField(label='Departamento', max_length=60, required=False)
    provincia = forms.CharField(label='Provincia', max_length=60, required=False)
    distrito = forms.CharField(label='Distrito', max_length=60, required=False)

    CAMPOS_DE_ENVIO = (
        ('direccion', 'la direccion'),
        ('departamento', 'el departamento'),
        ('provincia', 'la provincia'),
        ('distrito', 'el distrito'),
    )

    def __init__(self, *args, puntos=None, **kwargs):
        from .models import Pedido, PuntoRecojo

        super().__init__(*args, **kwargs)
        self.fields['modo_entrega'].choices = Pedido.MODO_ENTREGA_CHOICES
        self.fields['punto_recojo'].queryset = (
            puntos if puntos is not None else PuntoRecojo.objects.filter(activo=True)
        )
        self.fields['modo_entrega'].initial = Pedido.ENVIO

    def clean(self):
        """ Cada modo pide sus propios datos y ninguno pide los del otro """
        from .models import Pedido

        datos = super().clean()
        modo = datos.get('modo_entrega')

        if modo == Pedido.RECOJO:
            if not datos.get('punto_recojo'):
                self.add_error('punto_recojo', 'Elige donde vas a retirar tu pedido.')
            # lo que se escribio en la direccion no aplica: se descarta para que
            # el pedido no guarde una direccion de envio que nadie va a usar
            for campo, _ in self.CAMPOS_DE_ENVIO:
                datos[campo] = ''
            datos['referencia'] = ''

        elif modo == Pedido.ENVIO:
            datos['punto_recojo'] = None
            for campo, etiqueta in self.CAMPOS_DE_ENVIO:
                if not datos.get(campo):
                    self.add_error(campo, f'Para el envio a domicilio hace falta {etiqueta}.')

        return datos


class PagoForm(forms.Form):
    """
    Lo que el cliente declara despues de transferir.

    Todo aca es declaracion: el comprobante se valida despues mirando la cuenta
    real. Estas validaciones solo evitan que llegue basura obvia a esa revision.
    """

    MAX_VOUCHER = 1024 * 1024      # 1 MB, como pide la mayoria de estas pantallas

    cuenta = forms.ModelChoiceField(
        queryset=None, label='A que cuenta pagaste',
        widget=forms.RadioSelect, empty_label=None,
    )
    monto_declarado = forms.DecimalField(
        label='Monto que transferiste', max_digits=10, decimal_places=2, min_value=0.01
    )
    nro_operacion = forms.CharField(
        label='Numero de operacion', max_length=40,
        widget=forms.TextInput(attrs={'placeholder': 'El que te dio tu banco o Yape'})
    )
    fecha_pago = forms.DateField(label='Fecha del pago', widget=DateInput())
    voucher = forms.ImageField(label='Captura del comprobante')

    def __init__(self, *args, cuentas=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['cuenta'].queryset = cuentas

    def clean_fecha_pago(self):
        from django.utils import timezone

        fecha = self.cleaned_data['fecha_pago']
        if fecha > timezone.localdate():
            raise forms.ValidationError('Esa fecha todavia no llego. Revisa tu comprobante.')
        return fecha

    def clean_voucher(self):
        # ImageField ya verifico con Pillow que sea una imagen de verdad y no un
        # archivo renombrado; aca solo queda el tamano
        voucher = self.cleaned_data['voucher']
        if voucher.size > self.MAX_VOUCHER:
            raise forms.ValidationError(
                f'La imagen pesa {voucher.size / 1024 / 1024:.1f} MB. El maximo es 1 MB: '
                'reduce la captura o recortala.'
            )
        return voucher

    def clean(self):
        """ El mismo comprobante no se declara dos veces """
        from .models import Pago

        datos = super().clean()
        cuenta, nro = datos.get('cuenta'), (datos.get('nro_operacion') or '').strip()
        if cuenta and nro and Pago.objects.filter(cuenta=cuenta, nro_operacion=nro).exists():
            self.add_error(
                'nro_operacion',
                'Ese numero de operacion ya fue registrado. Revisa que sea el correcto.'
            )
        return datos


class ValidacionForm(forms.Form):
    """
    Para validar hay que escribir el monto que se vio en la cuenta.

    Si hubiera un boton "Aceptar" que tomara el monto declarado por el cliente,
    en tres semanas nadie estaria mirando el extracto. Es el mismo criterio con
    el que la boveda confirma una remesa: contando, no aceptando.
    """
    monto_confirmado = forms.DecimalField(
        label='Monto que viste en tu cuenta', max_digits=10, decimal_places=2, min_value=0.01,
        help_text='El de tu extracto o la notificacion de Yape. No el que declaro el cliente.',
    )


class RechazoForm(forms.Form):
    """ Rechazar exige un motivo porque el cliente lo va a leer """
    motivo = forms.CharField(
        label='Por que lo rechazas',
        widget=forms.Textarea(attrs={'rows': 3}),
        help_text='El cliente lo va a leer, asi que escribilo pensando en el.',
    )
