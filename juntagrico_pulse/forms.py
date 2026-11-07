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
            'comments': 'Kommentar',
        }
        widgets = {
            'participation_level': forms.NumberInput(attrs={'class': 'pulse-number-input'}),
            'people_eating_product': forms.NumberInput(attrs={'class': 'pulse-number-input'}),
            # bounds and step are validated on the model; autocomplete=off keeps the browser from
            # restoring a value on reload, which fill.html would treat as untouched
            'subscription_percent': forms.NumberInput(
                attrs={'type': 'range', 'class': 'custom-range', 'min': 5, 'max': 100, 'step': 5,
                       'autocomplete': 'off'}
            ),
        }

    def __init__(self, *args, season_ended=False, plural=False, **kwargs):
        super().__init__(*args, **kwargs)
        # address the members in plural if the result covers more than one
        you = 'euch' if plural else 'dir'
        you_receive = 'ihr erhaltet' if plural else 'du erhältst'

        participation_level = self.fields['participation_level']
        participation_level.required = True
        participation_level.label = (
            f'Wie viele Personen ab 18 Jahren, inklusive {you}, haben diese Saison an mindestens einem '
            f'Einsatz teilgenommen{"" if season_ended else " oder planen es noch"}?'
        )
        participation_level.help_text = (
            'Zähle alle Personen, die heute (am Tag, an dem du die Umfrage ausfüllst) mindestens 18 Jahre alt '
            'sind und diese Saison mindestens einmal an einem Einsatz mitgemacht haben'
            f'{"" if season_ended else " oder es noch vorhaben"} — unabhängig davon, ob sie vom Gemüse essen.'
        )
        people_eating_product = self.fields['people_eating_product']
        people_eating_product.label = (
            f'Wie viele Personen, Kinder eingeschlossen, essen regelmässig vom Gemüse, das {you_receive}?'
        )
        people_eating_product.help_text = (
            f'Zähle alle Personen, Kinder eingeschlossen, die sich regelmässig vom Gemüse ernähren, das '
            f'{you_receive} — unabhängig davon, ob sie an einem Einsatz mitgemacht haben. '
            'Die nächste Frage bezieht sich auf diese Personen.'
        )
        self.fields['subscription_percent'].label = (
            f'Für diese Personen: Wie viel des Gemüses, das zu Hause gegessen wird, deckt das Gemüse ab, '
            f'das {you_receive}?'
        )

    def clean(self):
        # the estimates are only asked for (and stored by SurveyResult.save()) with a subscription
        cleaned_data = super().clean()
        if self.instance.subscription_id is not None:
            for field in ('people_eating_product', 'subscription_percent'):
                if cleaned_data.get(field) is None and field not in self.errors:
                    self.add_error(field, 'Bitte gib eine Schätzung an.')
        return cleaned_data
