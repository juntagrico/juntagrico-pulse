import datetime
import html

from django.contrib import admin
from django.contrib.auth.models import Permission, AnonymousUser
from django.contrib.messages import get_messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, Http404
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse, resolve
from django.utils import timezone
from django.utils.html import strip_tags

from juntagrico.config import Config
from juntagrico.tests import JuntagricoTestCase
from juntagrico_pulse.admin import SurveyAdmin, SurveyProductInline, SurveyResultAdmin
from juntagrico_pulse.forms import BaseSurveyProductFormSet, SurveyForm, SurveyProductFormSet, SurveyResponseForm
from juntagrico_pulse.middleware import RequireSurveyCompletionMiddleware
from juntagrico_pulse.models import Survey, SurveyProduct, SurveyProductCoverage, SurveyResult, SurveyResultMember
from juntagrico_pulse.templatetags.juntagrico_pulse.menu import show_pulse_menu
from juntagrico_pulse.views import get_draft


def page_text(response):
    # visible text of a response, with tags removed and whitespace collapsed
    return ' '.join(html.unescape(strip_tags(response.content.decode())).split())


class JuntagricoPulseTest(JuntagricoTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.load_members()
        cls.set_up_depots()
        cls.set_up_sub_types()
        cls.set_up_sub()

        cls.member.user.user_permissions.add(
            Permission.objects.get(codename='view_survey'),
            Permission.objects.get(codename='change_survey'),
        )
        cls.default_member = cls.member

    def create_product(self, survey, name='Gemüse', bundles=None, sort_order=0):
        product = SurveyProduct.objects.create(survey=survey, name=name, sort_order=sort_order)
        product.bundles.set(bundles or [self.bundle])
        return product

    @staticmethod
    def coverage_key(product):
        return f'coverage_{product.pk}'

    def test_models_str_and_properties(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.DRAFT)
        self.assertEqual(str(survey), 'Umfrage 2026')

        # SurveyResultManager create_with_members with empty list
        empty_result = SurveyResult.objects.create_with_members(survey, [])
        self.assertIsNone(empty_result)

        # SurveyResultManager create_with_members with members
        result = SurveyResult.objects.create_with_members(
            survey, [self.member, self.member2]
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.members.count(), 2)
        self.assertFalse(result.is_complete)

        result.submitted_at = timezone.now()
        result.save()
        self.assertTrue(result.is_complete)

    def test_start_survey_form(self):
        form = SurveyForm()
        self.assertEqual(form.fields['year'].label, 'Umfragejahr')
        self.assertEqual(form.helper.inputs[0].value, 'Speichern')

        custom_form = SurveyForm(submit_text='Entwurf erstellen')
        self.assertEqual(custom_form.helper.inputs[0].value, 'Entwurf erstellen')

    def test_survey_response_form(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)
        vegetables = self.create_product(survey)
        eggs = self.create_product(survey, 'Eier', sort_order=1)

        # Form with subscription
        result_sub = SurveyResult.objects.create(survey=survey, subscription=self.sub)
        products = result_sub.products()
        form = SurveyResponseForm(
            data={'participation_level': 2},
            instance=result_sub,
            products=products,
        )
        self.assertFalse(form.is_valid())
        for field in ('people_eating_product', 'children_eating_product',
                      self.coverage_key(vegetables), self.coverage_key(eggs)):
            self.assertEqual(form.errors[field], ['Bitte gib eine Schätzung an.'], field)
        self.assertEqual([field.label for field in form.coverage_fields()], ['Gemüse', 'Eier'])

        valid_data = {
            'participation_level': 3,
            'people_eating_product': 4,
            'children_eating_product': 2,
            self.coverage_key(vegetables): 50,
            self.coverage_key(eggs): 100,
            'comments': 'Great veggies!',
        }
        form_valid = SurveyResponseForm(data=valid_data, instance=result_sub, products=products)
        self.assertTrue(form_valid.is_valid(), form_valid.errors)
        saved_result = form_valid.save(commit=True)
        saved_result.refresh_from_db()
        self.assertEqual(saved_result.people_eating_product, 4)
        self.assertEqual(saved_result.children_eating_product, 2)
        self.assertEqual(saved_result.people_equivalent, 5)  # 4 + 2 * 0.5
        self.assertEqual(
            dict(saved_result.product_coverages.values_list('product__name', 'percent')),
            {'Gemüse': 50, 'Eier': 100},
        )

        # percent must be a step of the slider
        form_step = SurveyResponseForm(
            data={**valid_data, self.coverage_key(vegetables): 73}, instance=result_sub, products=products
        )
        self.assertIn(self.coverage_key(vegetables), form_step.errors)

        # at least one person must eat the products
        form_nobody = SurveyResponseForm(
            data={**valid_data, 'people_eating_product': 0, 'children_eating_product': 0},
            instance=result_sub,
            products=products,
        )
        self.assertIn('people_eating_product', form_nobody.errors)

        # children alone are enough
        form_children = SurveyResponseForm(
            data={**valid_data, 'people_eating_product': 0}, instance=result_sub, products=products
        )
        self.assertTrue(form_children.is_valid(), form_children.errors)

        # a product that no longer applies keeps its stored answer
        form_fewer = SurveyResponseForm(
            data={**valid_data, self.coverage_key(vegetables): 75}, instance=result_sub, products=[vegetables]
        )
        self.assertTrue(form_fewer.is_valid(), form_fewer.errors)
        form_fewer.save()
        self.assertEqual(
            dict(result_sub.product_coverages.values_list('product__name', 'percent')),
            {'Gemüse': 75, 'Eier': 100},
        )

        # Form without subscription
        result_no_sub = SurveyResult.objects.create(survey=survey)
        form_no_sub = SurveyResponseForm(
            data={'participation_level': 1, 'people_eating_product': 4, self.coverage_key(vegetables): 50,
                  'comments': 'No sub'},
            instance=result_no_sub,
            products=result_no_sub.products(),
        )
        self.assertNotIn('people_eating_product', form_no_sub.fields)
        self.assertEqual(form_no_sub.coverage_fields(), [])
        self.assertTrue(form_no_sub.is_valid())
        saved_no_sub = form_no_sub.save()
        saved_no_sub.refresh_from_db()
        self.assertIsNone(saved_no_sub.people_eating_product)
        self.assertIsNone(saved_no_sub.people_equivalent)
        self.assertFalse(saved_no_sub.product_coverages.exists())

    def test_result_products(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)
        vegetables = self.create_product(survey)
        bread = self.create_product(survey, 'Brot', bundles=[self.unused_bundle], sort_order=1)
        other_survey = Survey.objects.create(year=2025, status=Survey.Status.CLOSED)
        self.create_product(other_survey)

        # only products of this survey with a bundle of an active part; sub has two active parts of self.bundle
        result = SurveyResult.objects.create(survey=survey, subscription=self.sub)
        self.assertEqual(list(result.products()), [vegetables])

        # parts of all selected bundles count
        today = datetime.date.today()
        bread_type = self.create_sub_type(self.unused_bundle)
        both = self.create_sub(self.depot, [self.sub_type, bread_type], today - datetime.timedelta(10))
        result_both = SurveyResult.objects.create(survey=survey, subscription=both)
        self.assertEqual(list(result_both.products()), [vegetables, bread])

        # parts that aren't active (yet or anymore) don't count
        deactivated = self.create_sub(
            self.depot, bread_type, today - datetime.timedelta(10), deactivation_date=today - datetime.timedelta(1)
        )
        result_deactivated = SurveyResult.objects.create(survey=survey, subscription=deactivated)
        self.assertEqual(list(result_deactivated.products()), [])
        waiting = SurveyResult.objects.create(survey=survey, subscription=self.sub2)
        self.assertEqual(list(waiting.products()), [])

        # no products without a subscription
        self.assertEqual(list(SurveyResult.objects.create(survey=survey).products()), [])

    def test_overview_template(self):
        Survey.objects.create(year=2026, status=Survey.Status.DRAFT)
        Survey.objects.create(year=2025, status=Survey.Status.PUBLISHED)
        Survey.objects.create(year=2024, status=Survey.Status.CLOSED)
        self.assertGet(reverse('juntagrico-pulse:overview'))

    def test_overview_empty_template(self):
        self.assertGet(reverse('juntagrico-pulse:overview'))

    def test_overview_permission_denied_for_regular_member(self):
        response = self.assertGet(
            reverse('juntagrico-pulse:overview'),
            member=self.member2,
            code=302,
        )
        self.assertIn('/login/', response.url)

    def test_start_template(self):
        self.assertGet(reverse('juntagrico-pulse:start'))

    def test_start_survey_post_success(self):
        response = self.assertPost(
            reverse('juntagrico-pulse:start'),
            data={'year': 2026, 'introduction': 'Welcome to survey 2026'},
            code=302,
        )
        survey = Survey.objects.get(year=2026)
        self.assertRedirects(
            response,
            reverse('juntagrico-pulse:review', kwargs={'survey_id': survey.pk}),
        )
        self.assertEqual(survey.introduction, 'Welcome to survey 2026')
        self.assertTrue(
            SurveyResult.objects.filter(
                survey=survey, subscription=self.sub
            ).exists()
        )
        self.assertTrue(
            SurveyResult.objects.filter(
                survey=survey, subscription=self.sub2
            ).exists()
        )
        self.assertTrue(
            SurveyResult.objects.filter(
                survey=survey, subscription__isnull=True
            ).exists()
        )

    def test_start_survey_post_invalid(self):
        response = self.assertPost(
            reverse('juntagrico-pulse:start'),
            data={'year': 1999},  # Invalid year < 2000
            code=200,
        )
        self.assertIn('year', response.context['form'].errors)

    def test_get_draft_helper(self):
        survey_draft = Survey.objects.create(year=2026, status=Survey.Status.DRAFT)
        self.assertEqual(get_draft(survey_draft.pk), survey_draft)

        survey_pub = Survey.objects.create(year=2025, status=Survey.Status.PUBLISHED)
        with self.assertRaises(PermissionDenied):
            get_draft(survey_pub.pk)

        with self.assertRaises(Http404):
            get_draft(99999)

    def test_review_template(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.DRAFT)
        self.assertGet(
            reverse('juntagrico-pulse:review', kwargs={'survey_id': survey.pk})
        )

    @staticmethod
    def product_data(products=(), extra=None):
        # POST data of the products formset on the review page, with an empty extra form
        data = {
            'products-TOTAL_FORMS': len(products) + 1,
            'products-INITIAL_FORMS': sum(1 for product in products if product.get('id')),
            'products-MIN_NUM_FORMS': 0,
            'products-MAX_NUM_FORMS': 1000,
        }
        # the browser posts the initial sort order of the extra form too
        for i, product in enumerate([*products, extra or {'sort_order': 0}]):
            for key, value in product.items():
                data[f'products-{i}-{key}'] = value
        return data

    def test_review_post_valid_and_invalid(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.DRAFT)
        url = reverse('juntagrico-pulse:review', kwargs={'survey_id': survey.pk})

        # Valid POST update
        response = self.assertPost(
            url,
            data={'year': 2027, 'introduction': 'Updated intro', **self.product_data()},
            code=302,
        )
        self.assertRedirects(response, url)
        survey.refresh_from_db()
        self.assertEqual(survey.year, 2027)
        self.assertEqual(survey.introduction, 'Updated intro')
        self.assertFalse(survey.products.exists())

        # Invalid POST update
        response_invalid = self.assertPost(
            url,
            data={'year': 1990, **self.product_data()},
            code=200,
        )
        self.assertIn('year', response_invalid.context['form'].errors)

        # the product errors are shown together with the survey errors
        response_both = self.assertPost(
            url,
            data={'year': 1990, **self.product_data(extra={'name': 'Eier', 'sort_order': 0})},
            code=200,
        )
        self.assertIn('year', response_both.context['form'].errors)
        self.assertIn('bundles', response_both.context['product_formset'].forms[0].errors)

    def test_review_products(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.DRAFT)
        SurveyResult.objects.create_with_members(survey, [self.member2], subscription=self.sub)
        url = reverse('juntagrico-pulse:review', kwargs={'survey_id': survey.pk})
        survey_data = {'year': 2026, 'introduction': ''}

        # add a product
        self.assertPost(url, data={**survey_data, **self.product_data(
            extra={'name': 'Gemüse', 'bundles': [self.bundle.pk], 'sort_order': 0},
        )}, code=302)
        vegetables = survey.products.get()
        self.assertEqual(vegetables.name, 'Gemüse')
        self.assertEqual(list(vegetables.bundles.all()), [self.bundle])

        # the page shows how many results the products are asked in
        self.create_product(survey, 'Brot', bundles=[self.unused_bundle], sort_order=1)
        response = self.assertGet(url)
        self.assertEqual(
            [getattr(form, 'result_count', None) for form in response.context['product_formset']],
            [1, 0, None],
        )
        self.assertIn('Wird bei 1 Antwort gefragt.', page_text(response))
        self.assertIn('Wird bei 0 Antworten gefragt.', page_text(response))

        # edit one, delete the other
        bread = survey.products.get(name='Brot')
        self.assertPost(url, data={**survey_data, **self.product_data([
            {'id': vegetables.pk, 'name': 'Gemüse & Kräuter', 'bundles': [self.bundle.pk, self.unused_bundle.pk],
             'sort_order': 0},
            {'id': bread.pk, 'name': 'Brot', 'bundles': [self.unused_bundle.pk], 'sort_order': 1, 'DELETE': 'on'},
        ])}, code=302)
        vegetables.refresh_from_db()
        self.assertEqual(vegetables.name, 'Gemüse & Kräuter')
        self.assertEqual(vegetables.bundles.count(), 2)
        self.assertEqual(list(survey.products.all()), [vegetables])

        # a product needs a bundle and a unique name; nothing is saved then
        existing = {'id': vegetables.pk, 'name': 'Gemüse & Kräuter', 'bundles': [self.bundle.pk], 'sort_order': 0}
        response = self.assertPost(url, data={**survey_data, 'year': 2027, **self.product_data(
            [existing], extra={'name': 'Eier', 'sort_order': 0},
        )}, code=200)
        self.assertIn('bundles', response.context['product_formset'].forms[1].errors)
        response = self.assertPost(url, data={**survey_data, 'year': 2027, **self.product_data(
            [existing], extra={'name': 'Gemüse & Kräuter', 'bundles': [self.bundle.pk], 'sort_order': 1},
        )}, code=200)
        self.assertTrue(response.context['product_formset'].non_form_errors())
        survey.refresh_from_db()
        self.assertEqual(survey.year, 2026)
        self.assertEqual(survey.products.count(), 1)

        # products with answers (from before the survey went back to draft) keep their bundles and can't be deleted
        result = survey.results.get()
        SurveyProductCoverage.objects.create(result=result, product=vegetables, percent=50)
        saved = {**existing, 'bundles': [self.bundle.pk, self.unused_bundle.pk]}
        response = self.assertPost(url, data={**survey_data, **self.product_data([existing])}, code=200)
        self.assertIn('bundles', response.context['product_formset'].forms[0].errors)
        # also reported together with errors of other products
        response = self.assertPost(url, data={**survey_data, **self.product_data([
            {**saved, 'DELETE': 'on'},
        ], extra={'name': 'Eier', 'sort_order': 0})}, code=200)
        self.assertIn('hat bereits Antworten', str(response.context['product_formset'].non_form_errors()))
        self.assertIn('bundles', response.context['product_formset'].forms[1].errors)
        # the heading keeps the saved year when the input is rejected
        response = self.assertPost(url, data={**survey_data, 'year': 1990, **self.product_data([saved])}, code=200)
        self.assertIn('Umfrage 2026 überprüfen', page_text(response))
        # the name can still be changed
        self.assertPost(url, data={**survey_data, **self.product_data([{**saved, 'name': 'Gemüse'}])}, code=302)
        vegetables.refresh_from_db()
        self.assertEqual(vegetables.name, 'Gemüse')
        self.assertEqual(vegetables.bundles.count(), 2)

        # products can't be changed once the survey is published
        survey.status = Survey.Status.PUBLISHED
        survey.save()
        self.assertPost(url, data={**survey_data, **self.product_data()}, code=403)

    def test_add_survey_member(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.DRAFT)

        # Add inactive member -> 404
        self.assertPost(
            reverse(
                'juntagrico-pulse:add_member',
                kwargs={'survey_id': survey.pk, 'member_id': self.inactive_member.pk},
            ),
            code=404,
        )

        # Add active member without subscription
        response_no_sub = self.assertPost(
            reverse(
                'juntagrico-pulse:add_member',
                kwargs={'survey_id': survey.pk, 'member_id': self.member4.pk},
            ),
            code=302,
        )
        self.assertRedirects(
            response_no_sub,
            reverse('juntagrico-pulse:review', kwargs={'survey_id': survey.pk}),
        )
        self.assertTrue(
            SurveyResultMember.objects.filter(
                result__survey=survey, member=self.member4
            ).exists()
        )

        # Add active member with subscription
        self.assertPost(
            reverse(
                'juntagrico-pulse:add_member',
                kwargs={'survey_id': survey.pk, 'member_id': self.member.pk},
            ),
            code=302,
        )
        self.assertTrue(
            SurveyResult.objects.filter(survey=survey, subscription=self.sub).exists()
        )

    def test_remove_survey_member(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.DRAFT)
        result = SurveyResult.objects.create_with_members(
            survey, [self.member, self.member2]
        )

        # Remove member2 from multi-member result (result should remain)
        url = reverse(
            'juntagrico-pulse:remove_member',
            kwargs={
                'survey_id': survey.pk,
                'result_id': result.pk,
                'member_id': self.member2.pk,
            },
        )
        response = self.assertPost(url, code=302)
        self.assertRedirects(
            response,
            reverse('juntagrico-pulse:review', kwargs={'survey_id': survey.pk}),
        )
        self.assertTrue(SurveyResult.objects.filter(pk=result.pk).exists())
        self.assertFalse(
            SurveyResultMember.objects.filter(
                result=result, member=self.member2
            ).exists()
        )

        # Remove last member (self.member) -> result should be deleted
        url_last = reverse(
            'juntagrico-pulse:remove_member',
            kwargs={
                'survey_id': survey.pk,
                'result_id': result.pk,
                'member_id': self.member.pk,
            },
        )
        self.assertPost(url_last, code=302)
        self.assertFalse(SurveyResult.objects.filter(pk=result.pk).exists())

    def test_publish_empty_survey(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.DRAFT)
        result = self.assertPost(
            reverse('juntagrico-pulse:publish', kwargs={'survey_id': survey.pk}),
            code=302,
        )
        self.assertRedirects(
            result, reverse('juntagrico-pulse:review', kwargs={'survey_id': survey.pk})
        )

    def test_publish_non_empty_survey(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.DRAFT)
        # Create a result for member2 (not the logged in test admin self.member) so middleware doesn't redirect
        SurveyResult.objects.create_with_members(survey, [self.member2])
        result = self.assertPost(
            reverse('juntagrico-pulse:publish', kwargs={'survey_id': survey.pk}),
            code=302,
        )
        self.assertRedirects(
            result, reverse('juntagrico-pulse:results', kwargs={'survey_id': survey.pk})
        )
        survey.refresh_from_db()
        self.assertEqual(survey.status, Survey.Status.PUBLISHED)
        warning = 'Es werden keine Fragen zum Versorgungsgrad gestellt, weil kein Produkt zu einem aktiven Bestandteil passt.'
        self.assertEqual([m.message for m in get_messages(result.wsgi_request)], [warning])

        def republish():
            survey.status = Survey.Status.DRAFT
            survey.save()
            self.client.cookies.pop('messages', None)  # drop the unread messages of the previous publish
            response = self.assertPost(reverse('juntagrico-pulse:publish', kwargs={'survey_id': survey.pk}), code=302)
            return [m.message for m in get_messages(response.wsgi_request)]

        # a product that no result is asked about warns as well
        self.create_product(survey)
        self.assertEqual(republish(), [warning])

        # no warning once a subscription is asked
        SurveyResult.objects.create(survey=survey, subscription=self.sub)
        self.assertEqual(republish(), [])

    def test_draft_survey(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)
        url = reverse('juntagrico-pulse:draft', kwargs={'survey_id': survey.pk})
        response = self.assertPost(url, code=302)
        self.assertRedirects(
            response,
            reverse('juntagrico-pulse:review', kwargs={'survey_id': survey.pk}),
        )
        survey.refresh_from_db()
        self.assertEqual(survey.status, Survey.Status.DRAFT)

        # Trying to set draft on already draft survey -> 404
        self.assertPost(url, code=404)

    def test_close_survey(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)
        url = reverse('juntagrico-pulse:close', kwargs={'survey_id': survey.pk})
        response = self.assertPost(url, code=302)
        self.assertRedirects(
            response,
            reverse('juntagrico-pulse:results', kwargs={'survey_id': survey.pk}),
        )
        survey.refresh_from_db()
        self.assertEqual(survey.status, Survey.Status.CLOSED)

        # Trying to close non-published survey -> 404
        self.assertPost(url, code=404)

    def test_edit_non_draft_survey_permission_denied(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)
        self.assertGet(
            reverse('juntagrico-pulse:review', kwargs={'survey_id': survey.pk}),
            code=403,
        )

    def test_fill_survey_get_and_post(self):
        survey = Survey.objects.create(
            year=2026,
            status=Survey.Status.PUBLISHED,
            introduction='<b>Intro text</b>',
        )
        vegetables = self.create_product(survey)
        eggs = self.create_product(survey, 'Eier', sort_order=1)
        self.create_product(survey, 'Brot', bundles=[self.unused_bundle], sort_order=2)
        result = SurveyResult.objects.create_with_members(
            survey, [self.member], subscription=self.sub
        )
        veg, egg = self.coverage_key(vegetables), self.coverage_key(eggs)

        fill_url = reverse('juntagrico-pulse:fill', kwargs={'survey_id': survey.pk})

        # GET by member
        response = self.assertGet(fill_url)
        self.assertContains(response, '<b>Intro text</b>')
        self.assertContains(response, 'Versorgungsgrad')
        # one slider per product of the subscription's bundles
        self.assertEqual(response.context['products'], [vegetables, eggs])
        self.assertContains(response, 'custom-range pulse-coverage-slider', count=2)
        self.assertIn('Für diese Personen: Wie viel von dem, was zu Hause gegessen wird', page_text(response))
        self.assertNotIn('Brot', page_text(response))

        # the sliders have no value until answered, so the page can tell they are untouched
        for key in (veg, egg):
            self.assertNotIn('value=', response.context['form'][key].as_widget())

        # GET by another member who is not part of this survey -> 404
        self.assertGet(fill_url, member=self.member2, code=404)

        # POST invalid by self.member
        response_invalid = self.assertPost(
            fill_url,
            # missing people, untouched sliders submit an empty percent
            data={'participation_level': 1, veg: '', egg: ''},
            code=200,
        )
        for field in ('people_eating_product', 'children_eating_product', veg, egg):
            self.assertFormError(response_invalid.context['form'], field, 'Bitte gib eine Schätzung an.')
        self.assertNotIn('value=', response_invalid.context['form'][veg].as_widget())

        # a POST without a percent at all is rejected the same way
        response_missing = self.assertPost(
            fill_url,
            data={'participation_level': 1, 'people_eating_product': 3, 'children_eating_product': 0, veg: 50},
            code=200,
        )
        self.assertFormError(response_missing.context['form'], egg, 'Bitte gib eine Schätzung an.')

        # POST with implausibly large numbers -> form errors
        response_too_large = self.assertPost(
            fill_url,
            data={'participation_level': 1000, 'people_eating_product': 1000, 'children_eating_product': 1000,
                  veg: 70, egg: 50},
            code=200,
        )
        form = response_too_large.context['form']
        self.assertIn('participation_level', form.errors)
        self.assertIn('people_eating_product', form.errors)
        self.assertIn('children_eating_product', form.errors)
        self.assertFalse(result.product_coverages.exists())

        # POST valid with safe next URL (the form posts back to the URL including ?next=)
        valid_data = {
            'participation_level': 2,
            'people_eating_product': 3,
            'children_eating_product': 1,
            veg: 70,
            egg: 25,
            'comments': 'All good',
        }
        response_valid = self.assertPost(f'{fill_url}?next=/my/profile', data=valid_data, code=302)
        self.assertRedirects(response_valid, '/my/profile')
        result.refresh_from_db()
        self.assertTrue(result.is_complete)
        self.assertEqual(result.submitted_by, self.member)
        self.assertEqual(result.people_eating_product, 3)
        self.assertEqual(result.children_eating_product, 1)
        self.assertEqual(
            dict(result.product_coverages.values_list('product', 'percent')),
            {vegetables.pk: 70, eggs.pk: 25},
        )

        # GET after already submitted shows info notice and the stored answers
        response_already = self.assertGet(fill_url)
        self.assertContains(
            response_already, 'Du hast bereits eine Antwort eingereicht.'
        )
        form = response_already.context['form']
        self.assertEqual(form['people_eating_product'].value(), 3)
        self.assertEqual(form['children_eating_product'].value(), 1)
        self.assertEqual(form[veg].value(), 70)
        self.assertIn('value="70"', form[veg].as_widget())

        # POST valid without next url -> redirects to 'home'
        response_no_next = self.assertPost(
            f'{fill_url}?next=https://evil.com/phishing',  # Unsafe next URL
            data={**valid_data, egg: 30},
            code=302,
        )
        self.assertRedirects(response_no_next, reverse('home'))
        self.assertEqual(result.product_coverages.get(product=eggs).percent, 30)

    def test_fill_survey_without_products(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)
        product = self.create_product(survey, bundles=[self.unused_bundle])
        fill_url = reverse('juntagrico-pulse:fill', kwargs={'survey_id': survey.pk})

        # with and without a subscription: no product of the survey applies
        for subscription in (self.sub, None):
            result = SurveyResult.objects.create_with_members(survey, [self.member], subscription=subscription)

            response = self.assertGet(fill_url)
            self.assertNotContains(response, 'Versorgungsgrad')

            response_post = self.assertPost(
                fill_url,
                # estimates are ignored without products
                data={'participation_level': 5, 'people_eating_product': 4, 'children_eating_product': 1,
                      self.coverage_key(product): 50, 'comments': 'No products'},
                code=302,
            )
            self.assertRedirects(response_post, reverse('home'))
            result.refresh_from_db()
            self.assertEqual(result.participation_level, 5)
            self.assertIsNone(result.people_eating_product)
            self.assertIsNone(result.children_eating_product)
            self.assertFalse(result.product_coverages.exists())
            result.delete()

        # answers from when a product still applied (e.g. before the part ended) are kept on resubmit
        result = SurveyResult.objects.create_with_members(
            survey, [self.member], subscription=self.sub, people_eating_product=3, children_eating_product=1,
        )
        SurveyProductCoverage.objects.create(result=result, product=product, percent=50)
        self.assertPost(fill_url, data={'participation_level': 2}, code=302)
        result.refresh_from_db()
        self.assertEqual((result.participation_level, result.people_eating_product), (2, 3))
        self.assertEqual(result.children_eating_product, 1)
        self.assertEqual(result.product_coverages.get().percent, 50)

    def test_survey_results_and_individual_results_view(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)

        # Completed subscription result
        r1 = SurveyResult.objects.create_with_members(
            survey, [self.member], subscription=self.sub
        )
        r1.submitted_at = timezone.now()
        r1.submitted_by = self.member
        r1.participation_level = 2
        r1.people_eating_product = 50
        r1.children_eating_product = 0
        r1.comments = 'Comment 1'
        r1.save()

        # Pending subscription result
        SurveyResult.objects.create(survey=survey, subscription=self.sub2)

        # Completed member result without sub
        r3 = SurveyResult.objects.create_with_members(survey, [self.member2])
        r3.submitted_at = timezone.now()
        r3.submitted_by = self.member2
        r3.participation_level = 1
        r3.save()

        # Pending member result without sub
        SurveyResult.objects.create(survey=survey)

        # Results summary view
        url_results = reverse(
            'juntagrico-pulse:results', kwargs={'survey_id': survey.pk}
        )
        response = self.assertGet(url_results)
        self.assertContains(response, 'Ergebnisse 2026')
        self.assertContains(response, 'Umfrage läuft noch')
        self.assertContains(response, 'participationChart')

        # Results view for closed survey
        survey.status = Survey.Status.CLOSED
        survey.save()
        response_closed = self.assertGet(url_results)
        self.assertContains(response_closed, 'Umfrage abgeschlossen')
        self.assertContains(response_closed, 'Umfrage erneut öffnen')

        # Individual results view
        url_individual = reverse(
            'juntagrico-pulse:results-individual', kwargs={'survey_id': survey.pk}
        )
        response_ind = self.assertGet(url_individual)
        self.assertContains(response_ind, 'Individuelle Antworten')
        self.assertContains(response_ind, 'Comment 1')

    def test_survey_results_coverage(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)
        vegetables = self.create_product(survey)
        eggs = self.create_product(survey, 'Eier', sort_order=1)
        self.create_product(survey, 'Brot', bundles=[self.unused_bundle], sort_order=2)

        def answer(subscription, adults, children, coverages, submitted=True):
            result = SurveyResult.objects.create(
                survey=survey,
                subscription=subscription,
                people_eating_product=adults,
                children_eating_product=children,
                submitted_at=timezone.now() if submitted else None,
            )
            for product, percent in coverages.items():
                SurveyProductCoverage.objects.create(result=result, product=product, percent=percent)

        # 2 adults + 2 children at 50% and 1 adult at 100%
        answer(self.sub, 2, 2, {vegetables: 50, eggs: 100})
        answer(self.sub2, 1, 0, {vegetables: 100})
        # unsubmitted answers don't count
        answer(self.sub3, 10, 10, {vegetables: 5, eggs: 5}, submitted=False)

        response = self.assertGet(reverse('juntagrico-pulse:results', kwargs={'survey_id': survey.pk}))
        self.assertEqual(response.context['people_summary'], {'adults': 3, 'children': 2})
        products = {product.name: product for product in response.context['products']}
        self.assertEqual(list(products), ['Gemüse', 'Eier', 'Brot'])

        vegetables = products['Gemüse']
        self.assertEqual((vegetables.people, vegetables.children), (5, 2))
        self.assertEqual(vegetables.equivalent_people, 4)  # 2 + 2 * 0.5 + 1
        self.assertEqual(vegetables.fed_equivalent, 2.5)  # 3 * 50% + 1 * 100%
        self.assertEqual(vegetables.average_percent, 62.5)

        eggs = products['Eier']
        self.assertEqual((eggs.people, eggs.children, eggs.fed_equivalent, eggs.average_percent), (4, 2, 3, 100))

        self.assertIsNone(products['Brot'].average_percent)
        text = page_text(response)
        self.assertIn(
            'Gemüse: Bei 5 Personen (davon 2 Kinder) deckt es durchschnittlich 63% des Bedarfs zu Hause ab. '
            'Das entspricht 2,5 ernährten Personen (Kinder zählen ×0,5).',
            text,
        )
        self.assertIn('Brot: Noch keine Antworten.', text)

    def test_survey_results_totals(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)
        SurveyResult.objects.create(survey=survey, subscription=self.sub, submitted_at=timezone.now())
        SurveyResult.objects.create(survey=survey, subscription=self.sub2)
        SurveyResult.objects.create(survey=survey, submitted_at=timezone.now())
        SurveyResult.objects.create(survey=survey)
        SurveyResult.objects.create(survey=survey)

        response = self.assertGet(reverse('juntagrico-pulse:results', kwargs={'survey_id': survey.pk}))
        stats = response.context['stats']
        self.assertEqual(stats['completed'], 2)
        self.assertEqual(stats['pending'], 3)

    def test_fill_survey_season_tense(self):
        # default business year: 1 January to 31 December
        current_year = datetime.date.today().year
        for year, started, ended, text in (
            (2000, True, True, 'begann und am 31. Dezember 2000 endete.'),
            (current_year, True, False, f'begann und am 31. Dezember {current_year} endet.'),
            (2999, False, False, 'beginnt und am 31. Dezember 2999 endet.'),
        ):
            survey = Survey.objects.create(year=year, status=Survey.Status.PUBLISHED)
            SurveyResult.objects.create_with_members(survey, [self.member])

            response = self.assertGet(reverse('juntagrico-pulse:fill', kwargs={'survey_id': survey.pk}))
            self.assertEqual(response.context['season_started'], started, year)
            self.assertEqual(response.context['season_ended'], ended, year)
            self.assertIn(text, page_text(response))
            self.assertEqual(
                'planen es' in response.context['form'].fields['participation_level'].label, not ended, year
            )
            survey.delete()

    def test_fill_survey_singular_plural(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)
        self.create_product(survey)
        fill_url = reverse('juntagrico-pulse:fill', kwargs={'survey_id': survey.pk})

        result = SurveyResult.objects.create_with_members(survey, [self.member], subscription=self.sub)
        response = self.assertGet(fill_url)
        fields = response.context['form'].fields
        self.assertFalse(response.context['plural'])
        self.assertNotIn('folgende Personen', page_text(response))
        self.assertIn('inklusive dir', fields['participation_level'].label)
        self.assertIn('älter als 12 Jahre aus deinem Haushalt', fields['people_eating_product'].label)
        self.assertIn('du erhältst', fields['people_eating_product'].label)
        self.assertIn('Kinder bis und mit 12 Jahren', fields['children_eating_product'].label)
        self.assertIn('das du erhältst?', page_text(response))
        self.assertIn('ausserhalb deines Haushalts, die über dich', fields['participation_level'].help_text)
        self.assertIn('heute (am Tag, an dem du die Umfrage ausfüllst) mindestens 18 Jahre', page_text(response))

        SurveyResultMember.objects.create(result=result, member=self.member2)
        response = self.assertGet(fill_url)
        fields = response.context['form'].fields
        self.assertTrue(response.context['plural'])
        self.assertIn('folgende Personen', page_text(response))
        self.assertIn(str(self.member), page_text(response))
        self.assertIn(str(self.member2), page_text(response))
        self.assertIn('inklusive euch', fields['participation_level'].label)
        self.assertIn('aus eurem Haushalt', fields['people_eating_product'].label)
        self.assertIn('das ihr erhaltet?', page_text(response))
        self.assertIn('ausserhalb eures Haushalts, die über euch', fields['participation_level'].help_text)

    def test_fill_survey_requires_participation_level(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)
        result = SurveyResult.objects.create_with_members(survey, [self.member])

        fill_url = reverse('juntagrico-pulse:fill', kwargs={'survey_id': survey.pk})
        response = self.assertPost(fill_url, data={}, code=200)
        self.assertIn('participation_level', response.context['form'].errors)
        result.refresh_from_db()
        self.assertFalse(result.is_complete)

    def test_participation_level_max_on_model(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)
        result = SurveyResult(survey=survey, participation_level=1000)
        with self.assertRaises(ValidationError):
            result.full_clean()

    def test_survey_results_distribution_ignores_unsubmitted(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)
        SurveyResult.objects.create(survey=survey, participation_level=2, submitted_at=timezone.now())
        SurveyResult.objects.create(survey=survey, participation_level=5)

        response = self.assertGet(reverse('juntagrico-pulse:results', kwargs={'survey_id': survey.pk}))
        distribution = response.context['distribution']
        self.assertEqual([row['participation_level'] for row in distribution], [0, 1, 2])
        self.assertEqual(distribution[2]['count'], 1)

    def test_survey_results_distribution_groups_large_levels(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)
        for level in (2, 12, 999):
            SurveyResult.objects.create(survey=survey, participation_level=level, submitted_at=timezone.now())

        response = self.assertGet(reverse('juntagrico-pulse:results', kwargs={'survey_id': survey.pk}))
        distribution = response.context['distribution']
        self.assertEqual([row['participation_level'] for row in distribution], [*range(10), '10+'])
        self.assertEqual(distribution[-1]['count'], 2)

    def test_individual_results_show_zero_answers(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)
        result = SurveyResult.objects.create_with_members(survey, [self.member], subscription=self.sub)
        result.submitted_at = timezone.now()
        result.participation_level = 0
        result.people_eating_product = 0
        result.children_eating_product = 1
        result.save()
        SurveyProductCoverage.objects.create(result=result, product=self.create_product(survey), percent=50)
        SurveyProductCoverage.objects.create(
            result=result, product=self.create_product(survey, 'Eier', sort_order=1), percent=100
        )

        response = self.assertGet(reverse('juntagrico-pulse:results-individual', kwargs={'survey_id': survey.pk}))
        self.assertContains(response, '<td>0</td>', html=True)
        self.assertContains(response, '<td>0 + 1 Kinder <br> Gemüse 50% · Eier 100%</td>', html=True)

    def test_survey_results_empty(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.CLOSED)
        url_results = reverse(
            'juntagrico-pulse:results', kwargs={'survey_id': survey.pk}
        )
        response = self.assertGet(url_results)
        self.assertContains(response, 'Noch keine Ergebnisse.')
        self.assertIsNone(response.context['participation_summary']['average'])
        self.assertIn(f'Durchschnitt pro {Config.vocabulary("subscription")}/Einzelperson: —', page_text(response))

        url_individual = reverse(
            'juntagrico-pulse:results-individual', kwargs={'survey_id': survey.pk}
        )
        response_ind = self.assertGet(url_individual)
        self.assertContains(
            response_ind, 'Für diese Umfrage sind keine'
        )

    def test_require_survey_completion_middleware(self):
        rf = RequestFactory()
        middleware = RequireSurveyCompletionMiddleware(lambda req: HttpResponse('OK'))

        # Anonymous user -> pass through
        req_anon = rf.get('/some-page/')
        req_anon.user = AnonymousUser()
        res = middleware(req_anon)
        self.assertEqual(res.content, b'OK')

        # Exempt URL (e.g. fill) -> pass through
        req_fill = rf.get(reverse('juntagrico-pulse:fill', args=[1]))
        req_fill.user = self.member.user
        res = middleware(req_fill)
        self.assertEqual(res.content, b'OK')

        # Unresolved 404 URL -> pass through
        req_404 = rf.get('/nonexistent-path-12345/')
        req_404.user = self.member.user
        res = middleware(req_404)
        self.assertEqual(res.content, b'OK')

        # Authenticated user with no pending surveys -> pass through
        req_page = rf.get('/my/profile')
        req_page.user = self.member.user
        res = middleware(req_page)
        self.assertEqual(res.content, b'OK')

        # Authenticated user with pending published survey -> redirected to fill
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)
        SurveyResult.objects.create_with_members(survey, [self.member])
        res_redirect = middleware(req_page)
        self.assertEqual(res_redirect.status_code, 302)
        expected_redirect = f"{reverse('juntagrico-pulse:fill', args=[survey.pk])}?next=%2Fmy%2Fprofile"
        self.assertEqual(res_redirect.url, expected_redirect)

    def test_show_pulse_menu_tag_and_menu_rendering(self):
        rf = RequestFactory()
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)
        SurveyResult.objects.create_with_members(survey, [self.member])

        request = rf.get('/')
        request.user = self.member.user
        context_data = show_pulse_menu(request)
        self.assertEqual(len(context_data['survey_results']), 1)

        # Render menu template
        template = Template('{% load juntagrico_pulse.menu %}{% show_pulse_menu request %}')
        rendered = template.render(Context({'request': request}))
        self.assertIn(f'/pulse/{survey.pk}/fill/', rendered)
        self.assertIn('Umfrage 2026', rendered)

    def test_template_error_edge_cases(self):
        rf = RequestFactory()
        request = rf.get('/pulse/')
        request.resolver_match = resolve('/pulse/')
        request.user = self.member.user

        survey = Survey.objects.create(year=2026, status=Survey.Status.DRAFT)
        result = SurveyResult.objects.create(survey=survey)

        # fill.html with non_field_errors
        form = SurveyResponseForm(data={}, instance=result)
        form.add_error(None, 'Custom non-field error message')

        rendered_fill = render_to_string(
            'juntagrico_pulse/fill.html',
            {
                'survey': survey,
                'result': result,
                'form': form,
                'business_year_start': datetime.date(2026, 1, 1),
                'business_year_end': datetime.date(2026, 12, 31),
                'already_submitted': False,
                'vocabulary': {'subscription': 'Abo', 'member': 'Mitglied'},
            },
            request=request,
        )
        self.assertIn('Custom non-field error message', rendered_fill)

        # review.html with error
        rendered_review = render_to_string(
            'juntagrico_pulse/review.html',
            {
                'survey': survey,
                'form': SurveyForm(instance=survey),
                'product_formset': SurveyProductFormSet(instance=survey, prefix='products'),
                'results': [],
                'excluded': [],
                'error': 'Review error occurred',
                'vocabulary': {
                    'subscription': 'Abo',
                    'member': 'Mitglied',
                    'member_pl': 'Mitglieder',
                },
            },
            request=request,
        )
        self.assertIn('Review error occurred', rendered_review)

        # individual_results.html with error
        rendered_ind = render_to_string(
            'juntagrico_pulse/individual_results.html',
            {
                'survey': survey,
                'results': [],
                'error': 'Individual results error occurred',
                'vocabulary': {
                    'subscription': 'Abo',
                    'member_pl': 'Mitglieder',
                },
            },
            request=request,
        )
        self.assertIn('Individual results error occurred', rendered_ind)

    def test_admin_custom_methods_and_actions(self):
        rf = RequestFactory()
        survey = Survey.objects.create(year=2026, status=Survey.Status.DRAFT)
        result1 = SurveyResult.objects.create_with_members(
            survey, [self.member, self.member2], subscription=self.sub
        )
        result1.submitted_at = timezone.now()
        result1.submitted_by = self.member
        result1.participation_level = 3
        result1.people_eating_product = 4
        result1.children_eating_product = 1
        result1.comments = 'Test comment'
        result1.save()
        SurveyProductCoverage.objects.create(result=result1, product=self.create_product(survey), percent=20)

        SurveyResult.objects.create(survey=survey)

        # SurveyAdmin tests
        survey_admin = SurveyAdmin(Survey, admin.site)
        request = rf.get('/')
        request.user = self.member.user
        qs = survey_admin.get_queryset(request)
        survey_obj = qs.get(pk=survey.pk)
        self.assertEqual(survey_admin.submitted_count(survey_obj), 1)
        self.assertEqual(survey_admin.result_count(survey_obj), 2)

        # products can only be changed in drafts
        inline = SurveyProductInline(Survey, admin.site)
        request.user.is_superuser = True  # has the model permissions, so only the status decides
        for status, allowed in ((Survey.Status.DRAFT, True), (Survey.Status.PUBLISHED, False),
                                (Survey.Status.CLOSED, False)):
            survey_obj.status = status
            self.assertEqual(inline.has_add_permission(request, survey_obj), allowed, status)
            self.assertEqual(inline.has_change_permission(request, survey_obj), allowed, status)
            self.assertEqual(inline.has_delete_permission(request, survey_obj), allowed, status)
        request.user.is_superuser = False
        self.assertIs(inline.get_formset(request, survey_obj).form._meta.model, SurveyProduct)
        self.assertTrue(issubclass(inline.get_formset(request, survey_obj), BaseSurveyProductFormSet))
        self.member.user.is_superuser = True
        self.member.user.is_staff = True
        self.member.user.save()
        self.assertGet(reverse('admin:juntagrico_pulse_survey_change', args=[survey.pk]))

        # SurveyResultAdmin tests
        result_admin = SurveyResultAdmin(SurveyResult, admin.site)
        qs_res = result_admin.get_queryset(request)
        res_obj = qs_res.get(pk=result1.pk)
        self.assertTrue(result_admin.is_complete(res_obj))
        self.assertIn(str(self.member), result_admin.members_display(res_obj))

        # Reset action
        from django.contrib.sessions.middleware import SessionMiddleware
        from django.contrib.messages.middleware import MessageMiddleware

        SessionMiddleware(lambda req: None).process_request(request)
        MessageMiddleware(lambda req: None).process_request(request)

        result_admin.reset_survey_results(
            request, SurveyResult.objects.filter(pk=result1.pk)
        )
        result1.refresh_from_db()
        self.assertIsNone(result1.submitted_at)
        self.assertIsNone(result1.submitted_by)
        self.assertIsNone(result1.participation_level)
        self.assertIsNone(result1.people_eating_product)
        self.assertIsNone(result1.children_eating_product)
        self.assertFalse(result1.product_coverages.exists())
        self.assertEqual(result1.comments, '')
        messages = [m.message for m in get_messages(request)]
        self.assertIn('1 Antwort(en) zurückgesetzt.', messages)
