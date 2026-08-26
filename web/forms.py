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

    nombre = forms.CharField(label='Nombres', max_length=60)
    apellidos = forms.CharField(label='Apellidos', max_length=60)
    email = forms.EmailField(label='Email')
    telefono = forms.CharField(label='Telefono', max_length=20)
    dni = forms.CharField(label='DNI', max_length=8, required=False)

    direccion = forms.CharField(label='Direccion', max_length=200)
    referencia = forms.CharField(
        label='Referencia', max_length=200, required=False,
        widget=forms.TextInput(attrs={'placeholder': 'Ej. frente al parque, casa azul'})
    )
    departamento = forms.CharField(label='Departamento', max_length=60)
    provincia = forms.CharField(label='Provincia', max_length=60)
    distrito = forms.CharField(label='Distrito', max_length=60)
