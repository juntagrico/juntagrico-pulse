import datetime

from django.contrib import admin
from django.contrib.auth.models import Permission, AnonymousUser
from django.contrib.messages import get_messages
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, Http404
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse, resolve
from django.utils import timezone

from juntagrico.tests import JuntagricoTestCase
from juntagrico_pulse.admin import SurveyAdmin, SurveyResultAdmin
from juntagrico_pulse.forms import SurveyForm, SurveyResponseForm
from juntagrico_pulse.middleware import RequireSurveyCompletionMiddleware
from juntagrico_pulse.models import Survey, SurveyResult, SurveyResultMember
from juntagrico_pulse.templatetags.juntagrico_pulse.menu import show_pulse_menu
from juntagrico_pulse.views import get_draft


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

        # Form with subscription
        result_sub = SurveyResult.objects.create(survey=survey, subscription=self.sub)
        form = SurveyResponseForm(
            data={'participation_level': 2},
            instance=result_sub,
            next_url='/home',
        )
        self.assertFalse(form.is_valid())
        self.assertIn('people_eating_product', form.errors)
        self.assertIn('subscription_percent', form.errors)

        valid_data = {
            'participation_level': 3,
            'people_eating_product': 4,
            'subscription_percent': 50,
            'comments': 'Great veggies!',
        }
        form_valid = SurveyResponseForm(
            data=valid_data,
            instance=result_sub,
            next_url='/home',
        )
        self.assertTrue(form_valid.is_valid())
        saved_result = form_valid.save(commit=True)
        self.assertEqual(saved_result.consumption_level, 2)  # 4 * 50 / 100 = 2

        # Form without subscription
        result_no_sub = SurveyResult.objects.create(survey=survey)
        form_no_sub = SurveyResponseForm(
            data={'participation_level': 1, 'comments': 'No sub'},
            instance=result_no_sub,
            next_url='',
        )
        self.assertTrue(form_no_sub.is_valid())
        saved_no_sub = form_no_sub.save(commit=False)
        self.assertIsNone(saved_no_sub.consumption_level)

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

    def test_review_post_valid_and_invalid(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.DRAFT)
        url = reverse('juntagrico-pulse:review', kwargs={'survey_id': survey.pk})

        # Valid POST update
        self.assertPost(
            url,
            data={'year': 2027, 'introduction': 'Updated intro'},
            code=200,
        )
        survey.refresh_from_db()
        self.assertEqual(survey.year, 2027)
        self.assertEqual(survey.introduction, 'Updated intro')

        # Invalid POST update
        response_invalid = self.assertPost(
            url,
            data={'year': 1990},
            code=200,
        )
        self.assertIn('year', response_invalid.context['form'].errors)

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
        result = SurveyResult.objects.create_with_members(
            survey, [self.member], subscription=self.sub
        )

        fill_url = reverse('juntagrico-pulse:fill', kwargs={'survey_id': survey.pk})

        # GET by member
        response = self.assertGet(fill_url)
        self.assertContains(response, '<b>Intro text</b>')
        self.assertContains(response, 'Versorgungsgrad')

        # GET by another member who is not part of this survey -> 404
        self.assertGet(fill_url, member=self.member2, code=404)

        # POST invalid by self.member
        response_invalid = self.assertPost(
            fill_url,
            data={'participation_level': 1},  # missing people_eating_product & percent
            code=200,
        )
        self.assertFormError(
            response_invalid.context['form'],
            'people_eating_product',
            'Bitte gib eine Schätzung an.',
        )

        # POST valid with safe next URL
        response_valid = self.assertPost(
            fill_url,
            data={
                'participation_level': 2,
                'people_eating_product': 3,
                'subscription_percent': 70,
                'comments': 'All good',
                'next': '/my/profile',
            },
            code=302,
        )
        self.assertRedirects(response_valid, '/my/profile')
        result.refresh_from_db()
        self.assertTrue(result.is_complete)
        self.assertEqual(result.submitted_by, self.member)

        # GET after already submitted shows info notice
        response_already = self.assertGet(fill_url)
        self.assertContains(
            response_already, 'Du hast bereits eine Antwort eingereicht.'
        )

        # POST valid without next url -> redirects to 'home'
        response_no_next = self.assertPost(
            fill_url,
            data={
                'participation_level': 2,
                'people_eating_product': 3,
                'subscription_percent': 70,
                'comments': 'All good',
                'next': 'https://evil.com/phishing',  # Unsafe next URL
            },
            code=302,
        )
        self.assertRedirects(response_no_next, reverse('home'))

    def test_fill_survey_without_subscription(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)
        result = SurveyResult.objects.create_with_members(survey, [self.member])
        fill_url = reverse('juntagrico-pulse:fill', kwargs={'survey_id': survey.pk})

        response = self.assertGet(fill_url)
        self.assertNotContains(response, 'Versorgungsgrad')

        response_post = self.assertPost(
            fill_url,
            data={'participation_level': 5, 'comments': 'No subscription'},
            code=302,
        )
        self.assertRedirects(response_post, reverse('home'))
        result.refresh_from_db()
        self.assertEqual(result.participation_level, 5)
        self.assertIsNone(result.consumption_level)

    def test_survey_results_and_individual_results_view(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)

        # Completed subscription result
        r1 = SurveyResult.objects.create_with_members(
            survey, [self.member], subscription=self.sub
        )
        r1.submitted_at = timezone.now()
        r1.submitted_by = self.member
        r1.participation_level = 2
        r1.consumption_level = 50
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
        self.assertEqual(stats['completed'], stats['completed_subscriptions'] + stats['completed_members'])
        self.assertEqual(stats['pending'], stats['pending_subscriptions'] + stats['pending_members'])

    def test_fill_survey_season_tense(self):
        for year, started, ended in ((2000, 'begann', 'endete'), (2999, 'beginnt', 'endet')):
            survey = Survey.objects.create(year=year, status=Survey.Status.PUBLISHED)
            SurveyResult.objects.create_with_members(survey, [self.member])

            response = self.assertGet(reverse('juntagrico-pulse:fill', kwargs={'survey_id': survey.pk}))
            text = ' '.join(response.content.decode().split())
            self.assertIn(f'</strong> {started} und am', text)
            self.assertIn(f'</strong> {ended}.', text)
            survey.delete()

    def test_survey_results_empty(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.CLOSED)
        url_results = reverse(
            'juntagrico-pulse:results', kwargs={'survey_id': survey.pk}
        )
        response = self.assertGet(url_results)
        self.assertContains(response, 'Noch keine Ergebnisse.')
        text = ' '.join(response.content.decode().split())
        self.assertIn('/Einzelperson: — </p>', text)

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
        form = SurveyResponseForm(data={}, instance=result, next_url='')
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
            survey, [self.member, self.member2]
        )
        result1.submitted_at = timezone.now()
        result1.submitted_by = self.member
        result1.participation_level = 3
        result1.consumption_level = 80
        result1.comments = 'Test comment'
        result1.save()

        SurveyResult.objects.create(survey=survey)

        # SurveyAdmin tests
        survey_admin = SurveyAdmin(Survey, admin.site)
        request = rf.get('/')
        request.user = self.member.user
        qs = survey_admin.get_queryset(request)
        survey_obj = qs.get(pk=survey.pk)
        self.assertEqual(survey_admin.submitted_count(survey_obj), 1)
        self.assertEqual(survey_admin.result_count(survey_obj), 2)

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
        self.assertIsNone(result1.consumption_level)
        self.assertEqual(result1.comments, '')
        messages = [m.message for m in get_messages(request)]
        self.assertIn('1 Antwort(en) zurückgesetzt.', messages)
