from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit
from django import forms
from djrichtextfield.widgets import RichTextWidget

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
        max_value=999,
        required=False,
        label='Wie viele erwachsene Personen ernähren sich regelmässig vom Gemüse, das ihr erhaltet?',
        widget=forms.NumberInput(attrs={'class': 'pulse-number-input'}),
    )
    subscription_percent = forms.IntegerField(
        min_value=5,
        max_value=100,
        required=False,
        label='Wie viel von eurem Gemüsebedarf deckt das Gemüse ab, das ihr erhaltet?',
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
            'participation_level': forms.NumberInput(attrs={'class': 'pulse-number-input'}),
        }

    def __init__(self, *args, season_ended=False, **kwargs):
        super().__init__(*args, **kwargs)
        participation_level = self.fields['participation_level']
        participation_level.required = True
        if season_ended:
            participation_level.label = ('Wie viele erwachsene Personen inklusive dir haben in dieser Saison '
                                         'an einem Einsatz teilgenommen?')

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
