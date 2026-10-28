from django.contrib.auth.models import Permission
from django.urls import reverse
from juntagrico.models import Member
from juntagrico.tests import JuntagricoTestCase
from juntagrico_pulse.forms import SurveyForm
from juntagrico_pulse.models import Survey


class JuntagricoPulseTest(JuntagricoTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.member = Member.objects.create(
            first_name='Test',
            last_name='User',
            email='test@example.com',
        )
        cls.member.user.user_permissions.add(
            Permission.objects.get(codename='view_survey'),
            Permission.objects.get(codename='change_survey'),
        )
        cls.default_member = cls.member

    def test_start_survey_form(self):
        form = SurveyForm()
        self.assertEqual(form.fields['year'].label, 'Umfragejahr')

    def test_overview_template(self):
        Survey.objects.create(year=2026, status=Survey.Status.DRAFT)
        self.assertGet(reverse('juntagrico-pulse:overview'))

    def test_overview_empty_template(self):
        self.assertGet(reverse('juntagrico-pulse:overview'))

    def test_start_template(self):
        self.assertGet(reverse('juntagrico-pulse:start'))

    def test_review_template(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.DRAFT)
        self.assertGet(
            reverse('juntagrico-pulse:review', kwargs={'survey_id': survey.pk})
        )

    def test_publish_empty_survey(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.DRAFT)
        result = self.assertPost(
            reverse('juntagrico-pulse:publish', kwargs={'survey_id': survey.pk}),
            code=302,
        )
        self.assertRedirects(
            result, reverse('juntagrico-pulse:review', kwargs={'survey_id': survey.pk})
        )

    def test_edit_non_draft_survey_permission_denied(self):
        survey = Survey.objects.create(year=2026, status=Survey.Status.PUBLISHED)
        self.assertGet(
            reverse('juntagrico-pulse:review', kwargs={'survey_id': survey.pk}),
            code=403,
        )
