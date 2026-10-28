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
    class Meta:
        model = SurveyResult
        fields = ['participation_level', 'people_eating_product', 'subscription_percent', 'comments']
        labels = {
            'participation_level': 'Wie viele erwachsene Personen inklusive dir haben diese Saison schon '
                                   'an einem Einsatz teilgenommen oder planen es diese Saison noch?',
            'people_eating_product': 'Wie viele erwachsene Personen ernähren sich regelmässig vom Gemüse, '
                                     'das ihr erhaltet?',
            'subscription_percent': 'Wie viel von eurem Gemüsebedarf deckt das Gemüse ab, das ihr erhaltet?',
            'comments': 'Kommentar',
        }
        widgets = {
            'participation_level': forms.NumberInput(attrs={'class': 'pulse-number-input'}),
            'people_eating_product': forms.NumberInput(attrs={'class': 'pulse-number-input'}),
            # bounds and step are validated on the model
            'subscription_percent': forms.NumberInput(
                attrs={'type': 'range', 'class': 'custom-range', 'min': 5, 'max': 100, 'step': 5}
            ),
        }

    def __init__(self, *args, season_ended=False, **kwargs):
        super().__init__(*args, **kwargs)
        participation_level = self.fields['participation_level']
        participation_level.required = True
        if season_ended:
            participation_level.label = ('Wie viele erwachsene Personen inklusive dir haben in dieser Saison '
                                         'an einem Einsatz teilgenommen?')

    def clean(self):
        # the estimates are only asked for (and stored by SurveyResult.save()) with a subscription
        cleaned_data = super().clean()
        if self.instance.subscription_id is not None:
            for field in ('people_eating_product', 'subscription_percent'):
                if cleaned_data.get(field) is None and field not in self.errors:
                    self.add_error(field, 'Bitte gib eine Schätzung an.')
        return cleaned_data
