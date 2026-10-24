from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit, Hidden
from django import forms

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
    class Meta:
        model = SurveyResult
        fields = (
            'participation_level',
            'consumption_level',
            'comments',
        )
        widgets = {
            'comments': forms.Textarea(attrs={'rows': 4}),
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
