from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit, Hidden
from django import forms
from juntagrico.config import Config

from juntagrico_pulse.models import SurveyResult


class StartSurveyForm(forms.Form):
    year = forms.IntegerField(
        min_value=2000,
        max_value=9999,
        label='Umfragejahr',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.add_input(
            Submit(
                'submit',
                'Entwurf erstellen',
            )
        )


class SurveyResponseForm(forms.ModelForm):
    # TODO: store these in DB to make it easier to reconstruct
    people_eating_product = forms.IntegerField(
        min_value=0,
        required=False,
        label='Wie viele erwachsene Personen essen mit?',
    )
    subscription_percent = forms.IntegerField(
        min_value=5,
        max_value=100,
        required=False,
        label=f'Wie viel von eurem Gemüsebedarf deckt das {Config.vocabulary("subscription")} ab?',
        widget=forms.NumberInput(
            attrs={
                'type': 'range',
                'class': 'custom-range',
                'min': 5,
                'max': 100,
                'step': 5,
            }
        ),
    )

    class Meta:
        model = SurveyResult
        fields = ['participation_level', 'comments']
        labels = {
            'participation_level': 'Mitarbeitende Personen',
            'comments': 'Kommentar',
        }

    def __init__(self, *args, next_url, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.add_input(Hidden('next_url', next_url))
        self.helper.add_input(
            Submit(
                'submit',
                'Absenden',
            )
        )

    def clean(self):
        cleaned_data = super().clean()
        has_subscription = self.instance.subscription_id is not None

        people = cleaned_data.get('people_eating_product')
        percent = cleaned_data.get('subscription_percent')

        if has_subscription:
            if people is None:
                self.add_error('people_eating_product', 'Bitte gib eine Schätzung an.')
            if percent is None:
                self.add_error('subscription_percent', 'Bitte gib eine Schätzung an.')
        else:
            cleaned_data['people_eating_product'] = None
            cleaned_data['subscription_percent'] = None

        return cleaned_data

    def save(self, commit=True):
        result = super().save(commit=False)

        if result.subscription_id is not None:
            people = self.cleaned_data['people_eating_product']
            percent = self.cleaned_data['subscription_percent']
            result.consumption_level = people * percent / 100
        else:
            result.consumption_level = None

        if commit:
            result.save()
            self.save_m2m()

        return result
