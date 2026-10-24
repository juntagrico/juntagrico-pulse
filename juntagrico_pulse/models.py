from django.db import models
from django.db.models import Q


class Survey(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Entwurf'
        PUBLISHED = 'published', 'Veröffentlicht'
        CLOSED = 'closed', 'Geschlossen'

    year = models.PositiveSmallIntegerField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    def __str__(self):
        return f'Umfrage {self.year}'


class SurveyResult(models.Model):
    survey = models.ForeignKey(
        Survey,
        on_delete=models.CASCADE,
        related_name='results',
    )
    subscription = models.ForeignKey(
        'juntagrico.Subscription',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='survey_results',
    )

    members = models.ManyToManyField(
        'juntagrico.Member',
        through='SurveyResultMember',
        related_name='covered_survey_results',
    )

    submitted_by = models.ForeignKey(
        'juntagrico.Member',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='submitted_survey_results',
    )
    submitted_at = models.DateTimeField(null=True, blank=True)

    # Fixed question fields; empty until submitted.
    participation_level = models.PositiveSmallIntegerField(null=True, blank=True)
    consumption_level = models.PositiveSmallIntegerField(null=True, blank=True)
    comments = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['survey', 'subscription'],
                condition=Q(subscription__isnull=False),
                name='unique_subscription_result_per_survey',
            ),
        ]

    @property
    def is_complete(self):
        return self.submitted_at is not None


class SurveyResultMember(models.Model):
    result = models.ForeignKey(
        SurveyResult,
        on_delete=models.CASCADE,
        related_name='member_links',
    )
    member = models.ForeignKey(
        'juntagrico.Member',
        on_delete=models.CASCADE,
        related_name='survey_result_links',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['result', 'member'],
                name='unique_member_per_survey_result',
            ),
        ]
        indexes = [
            models.Index(fields=['member', 'result']),
        ]
