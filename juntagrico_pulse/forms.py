from crispy_forms.helper import FormHelper
from crispy_forms.layout import Submit
from django import forms
from djrichtextfield.widgets import RichTextWidget
from juntagrico.entity.subtypes import SubscriptionBundle

from juntagrico_pulse.models import PERCENT_VALIDATORS, Survey, SurveyProduct, SurveyProductCoverage, SurveyResult


class SurveyForm(forms.ModelForm):
    class Meta:
        model = Survey
        fields = ['year', 'introduction']
        widgets = {
            'introduction': RichTextWidget(field_settings='juntagrico-pulse'),
        }

    def __init__(self, *args, submit_text=None, form_tag=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        # without a form tag, the surrounding template renders the form tag and the submit button
        self.helper.form_tag = form_tag
        if form_tag:
            self.helper.add_input(
                Submit(
                    'submit',
                    submit_text or 'Speichern',
                )
            )


class SurveyProductForm(forms.ModelForm):
    class Meta:
        model = SurveyProduct
        fields = ['name', 'bundles', 'sort_order']
        widgets = {
            'bundles': forms.CheckboxSelectMultiple,
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['name'].help_text = 'Wird in der Frage als Produktname verwendet.'
        self.fields['bundles'].queryset = SubscriptionBundle.objects.select_related('category')
        self.fields['bundles'].help_text = 'Die Frage wird nur bei aktiven Bestandteilen aus diesen Paketen gestellt.'

    def has_answers(self):
        # answers exist if the survey was published before it went back to draft
        return self.instance.pk is not None and self.instance.coverages.exists()

    def clean(self):
        cleaned_data = super().clean()
        if 'bundles' in self.changed_data and self.has_answers():
            self.add_error('bundles', 'Die Pakete eines Produkts mit Antworten können nicht geändert werden.')
        return cleaned_data


class BaseSurveyProductFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        # not self.deleted_forms, which is empty while any form is invalid
        for form in self.forms:
            if self._should_delete_form(form) and form.has_answers():
                raise forms.ValidationError(
                    f'«{form.instance.name}» hat bereits Antworten und kann nicht gelöscht werden.'
                )


SurveyProductFormSet = forms.inlineformset_factory(
    Survey,
    SurveyProduct,
    form=SurveyProductForm,
    formset=BaseSurveyProductFormSet,
    extra=1,
    can_delete=True,
)


class SurveyResponseForm(forms.ModelForm):
    COVERAGE_PREFIX = 'coverage_'

    class Meta:
        model = SurveyResult
        fields = ['participation_level', 'people_eating_product', 'children_eating_product', 'comments']
        labels = {
            'comments': 'Kommentar',
        }
        widgets = {
            'participation_level': forms.NumberInput(attrs={'class': 'pulse-number-input'}),
            'people_eating_product': forms.NumberInput(attrs={'class': 'pulse-number-input'}),
            'children_eating_product': forms.NumberInput(attrs={'class': 'pulse-number-input'}),
        }

    def __init__(self, *args, products=(), season_ended=False, plural=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.products = list(products)
        # address the members in plural if the result covers more than one
        you = 'euch' if plural else 'dir'
        your = 'eurem' if plural else 'deinem'
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
            f'{"" if season_ended else " oder es noch vorhaben"} — unabhängig davon, ob sie von den Produkten essen.'
        )
        if self.instance.subscription_id is not None:
            participation_level.help_text += (
                f' Dazu gehören auch Personen ausserhalb {"eures" if plural else "deines"} Haushalts, '
                f'die über {"euch" if plural else "dich"} mitarbeiten.'
            )

        if not self.products:
            # without products to ask for, the people eating them aren't asked either
            del self.fields['people_eating_product']
            del self.fields['children_eating_product']
            return

        people_eating_product = self.fields['people_eating_product']
        people_eating_product.label = (
            f'Wie viele Personen älter als 12 Jahre aus {your} Haushalt oder Freundeskreis essen regelmässig '
            f'von den Produkten, die {you_receive}?'
        )
        people_eating_product.help_text = (
            'Zähle diese Personen unabhängig davon, ob sie an einem Einsatz mitgemacht haben.'
        )
        children_eating_product = self.fields['children_eating_product']
        children_eating_product.label = 'Wie viele Kinder bis und mit 12 Jahren essen regelmässig davon?'
        children_eating_product.help_text = 'Gib 0 an, wenn keine Kinder mitessen.'
        for field in (people_eating_product, children_eating_product):
            field.required = True
            field.error_messages['required'] = 'Bitte gib eine Schätzung an.'

        self.coverage_intro = (
            f'Für diese Personen: Wie viel von dem, was zu Hause gegessen wird, deckt das ab, das {you_receive}?'
        )
        initial = {}
        if self.instance.pk:
            initial = dict(self.instance.product_coverages.values_list('product_id', 'percent'))
        for product in self.products:
            self.fields[self.coverage_field_name(product)] = forms.IntegerField(
                label=product.name,
                initial=initial.get(product.pk),
                validators=PERCENT_VALIDATORS,
                error_messages={'required': 'Bitte gib eine Schätzung an.'},
                # bounds and step are validated by PERCENT_VALIDATORS; autocomplete=off keeps the browser from
                # restoring a value on reload, which fill.html would treat as untouched
                widget=forms.NumberInput(
                    attrs={'type': 'range', 'class': 'custom-range pulse-coverage-slider',
                           'min': 5, 'max': 100, 'step': 5, 'autocomplete': 'off'}
                ),
            )

    def coverage_field_name(self, product):
        return f'{self.COVERAGE_PREFIX}{product.pk}'

    def coverage_fields(self):
        return [self[self.coverage_field_name(product)] for product in self.products]

    def clean(self):
        cleaned_data = super().clean()
        if self.products:
            adults = cleaned_data.get('people_eating_product')
            children = cleaned_data.get('children_eating_product')
            if adults is not None and children is not None and adults + children < 1:
                self.add_error('people_eating_product', 'Mindestens eine Person muss von den Produkten essen.')
        return cleaned_data

    def _save_m2m(self):
        # also called via save_m2m() after save(commit=False)
        super()._save_m2m()
        # answers for products that no longer apply (e.g. the part ended since) are kept
        for product in self.products:
            SurveyProductCoverage.objects.update_or_create(
                result=self.instance,
                product=product,
                defaults={'percent': self.cleaned_data[self.coverage_field_name(product)]},
            )
