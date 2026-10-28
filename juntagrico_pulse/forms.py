from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit, Hidden
from django import forms
from djrichtextfield.widgets import RichTextWidget
from juntagrico.config import Config

from juntagrico_pulse.models import SurveyResult, Survey


class SurveyForm(forms.ModelForm):
    class Meta:
        model = Survey
        fields = ['year', 'introduction']
        widgets = {
            'introduction': RichTextWidget(field_settings='juntagrico-pulse'),
        }

    def __init__(self, *args, submit_text=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.add_input(
            Submit(
                'submit',
                submit_text or 'Speichern',
            )
        )


class SurveyResponseForm(forms.ModelForm):
    # TODO: store these in DB to make it easier to reconstruct
    people_eating_product = forms.IntegerField(
        min_value=0,
        required=False,
        label=f'Wie viele erwachsene Personen ernähren sich regelmässig vom Gemüse in diesem {Config.vocabulary("subscription")}?',
        widget=forms.NumberInput(attrs={'style': 'max-width: 6rem;'}),
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
            'participation_level': 'Wie viele erwachsene Personen inklusive dir haben diese Saison schon '
                                   'an einem Einsatz teilgenommen oder planen es diese Saison noch?',
            'comments': 'Kommentar',
        }
        widgets = {
            'participation_level': forms.NumberInput(attrs={'style': 'max-width: 6rem;'}),
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
